"""Запросы к FASHN API. Секреты и изображения в лог не пишутся."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import random
from typing import Any

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://api.fashn.ai/v1"
POLL_INTERVAL_SEC = 3
POLL_ATTEMPTS = 40


class FashnError(Exception):
    def __init__(self, name: str, message: str) -> None:
        self.name = name
        self.message = message
        super().__init__(name)


def user_message(error: FashnError) -> str:
    blob = f"{error.name} {error.message}".lower()
    if error.name == "ContentModerationError":
        return "Изображение не прошло проверку. Пришлите другое фото."
    if error.name in {"ImageLoadError", "InputValidationError"}:
        return "Не удалось прочитать фото. Пришлите другое изображение."
    if error.name in {"UnauthorizedAccess", "Unauthorized"}:
        return "Сервис генерации недоступен. Проверьте FASHN_API_KEY."
    if error.name == "RateLimitExceeded" or "rate" in blob:
        return "Слишком много запросов. Попробуйте через минуту."
    if "credit" in blob or error.name in {"PaymentRequired", "InsufficientCredits"}:
        return "На счёте FASHN закончились кредиты."
    if error.name == "Timeout":
        return "Генерация заняла слишком много времени. Попробуйте ещё раз."
    if error.name == "NetworkError":
        return "Нет связи с сервисом генерации. Попробуйте ещё раз."
    return "Не получилось сгенерировать фото. Попробуйте ещё раз."


def _api_key() -> str:
    key = os.getenv("FASHN_API_KEY", "").strip()
    if not key:
        raise FashnError("ConfigError", "FASHN_API_KEY is missing")
    return key


def _error_from_body(body: dict[str, Any], fallback: str) -> FashnError:
    error = body.get("error")
    if isinstance(error, dict):
        name = str(error.get("name") or error.get("error") or fallback)
        message = str(error.get("message") or "")
        return FashnError(name, message[:300])
    if isinstance(error, str) and error:
        return FashnError(error, str(body.get("message") or "")[:300])
    message = body.get("message")
    if isinstance(message, str) and message:
        return FashnError(str(body.get("error") or fallback), message[:300])
    return FashnError(fallback, "")


async def _decode_output(client: httpx.AsyncClient, output: str) -> bytes:
    if output.startswith("data:"):
        _, encoded = output.split(",", 1)
        return base64.b64decode(encoded)
    response = await client.get(output)
    response.raise_for_status()
    return response.content


async def _run(client: httpx.AsyncClient, inputs: dict[str, Any], model_name: str) -> bytes:
    headers = {"Authorization": f"Bearer {_api_key()}"}
    try:
        created = await client.post(
            f"{API_BASE}/run",
            headers=headers,
            json={"model_name": model_name, "inputs": inputs},
        )
    except httpx.HTTPError as exc:
        logger.exception("FASHN run request failed: %s", type(exc).__name__)
        raise FashnError("NetworkError", type(exc).__name__) from exc

    if created.status_code >= 400:
        body = _json(created)
        logger.error("FASHN run HTTP %s: %s", created.status_code, _safe_error(body))
        raise _error_from_body(body, f"HTTP{created.status_code}")

    prediction_id = _json(created).get("id")
    if not isinstance(prediction_id, str) or not prediction_id:
        raise FashnError("BadResponse", "missing prediction id")

    for _ in range(POLL_ATTEMPTS):
        await asyncio.sleep(POLL_INTERVAL_SEC)
        try:
            status_response = await client.get(
                f"{API_BASE}/status/{prediction_id}",
                headers=headers,
            )
        except httpx.HTTPError as exc:
            logger.exception("FASHN status request failed: %s", type(exc).__name__)
            raise FashnError("NetworkError", type(exc).__name__) from exc

        body = _json(status_response)
        if status_response.status_code >= 400:
            logger.error("FASHN status HTTP %s: %s", status_response.status_code, _safe_error(body))
            raise _error_from_body(body, f"HTTP{status_response.status_code}")

        status = body.get("status")
        if status in {"starting", "in_queue", "processing"}:
            continue
        if status == "completed":
            output = body.get("output")
            if not isinstance(output, list) or not output or not isinstance(output[0], str):
                raise FashnError("BadResponse", "empty output")
            try:
                return await _decode_output(client, output[0])
            except (httpx.HTTPError, ValueError) as exc:
                logger.exception("FASHN output download failed: %s", type(exc).__name__)
                raise FashnError("OutputError", type(exc).__name__) from exc
        if status == "failed":
            error = _error_from_body(body, "GenerationFailed")
            logger.error("FASHN generation failed: %s", error.name)
            raise error
        logger.error("FASHN unexpected status")
        raise FashnError("BadResponse", str(status or ""))

    raise FashnError("Timeout", "polling timed out")


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _safe_error(body: dict[str, Any]) -> str:
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("name") or "error")
    if isinstance(error, str):
        return error[:80]
    return str(body.get("status") or "error")


# balanced точнее передаёт цвет и детали вещи, чем fast. На 1k это 2 токена FASHN.
_FIDELITY = {
    "resolution": "1k",
    "generation_mode": "balanced",
    "num_images": 1,
    "output_format": "png",
}


async def generate_from_garment(
    product_image: str,
    prompt: str,
    aspect_ratio: str = "2:3",
    pose_image: str = "",
    background_image: str = "",
    face_image: str = "",
) -> bytes:
    """Фото вещи → новый человек. pose задаёт стойку, face — лицо, background — комнату."""
    inputs = {
        "product_image": product_image,
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        **_FIDELITY,
    }
    if pose_image:
        inputs["image_prompt"] = pose_image
    if background_image:
        inputs["background_reference"] = background_image
    if face_image:
        inputs["face_reference"] = face_image
        inputs["face_reference_mode"] = "match_base"
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=120.0)) as client:
        return await _run(client, inputs, "product-to-model")


_KEEP = (
    "No person, no mannequin. Preserve the exact color, print, fabric, seams, pockets, "
    "buttons, zippers and length. Do not redesign the garment and do not invent details."
)
_HANGER_SCENES = (
    "a white hanger on a slim white clothing rail standing against the wall",
    "a matte black hanger on a slim white clothing rail standing against the wall",
    "a matte black hanger on a slim black clothing rail standing against the wall",
    "a light wood hanger on the open door of a pale wardrobe",
)
_PACKSHOT_PROMPTS = {
    "rail": (
        "Match the reference scene: the same rail, hangers, wall, floor, light and spacing. "
        "Hang only the user's garment, full length, front view, natural drape, as the clear hero. "
        "Replace the clothes from the reference. Do not copy the other garments. "
        f"{_KEEP}"
    ),
    "bed": (
        "Garment laid neatly on a made bed with simple light linen, soft daylight, "
        f"lifestyle marketplace photo. {_KEEP}"
    ),
    "white": (
        "Lay the garment flat on a seamless pure white background, straight overhead catalog shot, "
        "even soft light, only a faint soft shadow under the garment. "
        "No floorboards, no wall, no room, no horizon line, no props. "
        f"{_KEEP}"
    ),
    "floor": (
        "Lay the user's garment flat on the floor from the reference: light wood laminate with visible boards, "
        "soft daylight, a slight top-down angle so the surface reads as a real floor, not a white wall. "
        "Use the reference for the floor, the light and the angle only. Do not copy the garment in the reference. "
        f"{_KEEP}"
    ),
}


def _stand_prompt() -> str:
    hanger = random.choice(("wooden hanger", "slim metal hanger"))
    scene = random.choice((
        "bright minimalist home dressing corner",
        "small boutique corner",
        "bedroom by the window",
    ))
    surface = random.choice((
        "light plaster wall",
        "light oak floor",
        "beige wall with a baseboard",
    ))
    rack = random.choice(("matte white", "black metal", "light wood"))
    extra_garment = random.choice((
        "",
        " One more garment hangs on the rack, partly visible and out of focus.",
    ))
    side = random.choice(("left", "right"))
    lens = random.choice(("35mm", "50mm"))
    angle = random.choice(("straight-on", "5 degree off-axis"))
    props = ("plant", "shoe", "bag", "chair")
    first = random.choice(props)
    second = random.choice(tuple(item for item in props if item != first))
    return (
        "Use the attached photo as the exact product reference. "
        "Create a realistic photograph of this exact garment hanging on a "
        f"{hanger} on a freestanding floor clothing rack, as if shot by a person "
        "on a smartphone or mirrorless camera for a marketplace listing. "
        f"Scene: {scene} with {surface}. The rack is {rack}.{extra_garment} "
        f"Light: soft natural daylight from a large window on the {side}, plus gentle "
        "bounce light from the walls so the shadows stay light and the scene does not feel empty. "
        "Soft window shadow on the wall with natural falloff. Realistic soft contact shadow of the garment on the wall. "
        "Color temperature neutral to slightly warm, accurate white balance. "
        f"Camera: shot on a {lens} lens, f/2.8 to f/4, eye level, slight {angle} angle, "
        "vertical 3:4 frame, garment fully visible with some air around it. "
        "Tiny natural film or sensor grain, very slight vignette, no HDR look. "
        "Realism details: natural fabric folds and gravity-correct drape, small soft wrinkles on sleeves and hem, "
        "visible fabric texture, slightly uneven light across the wall, "
        f"a couple of real-life objects out of focus in the background, a {first} and a {second}, not staged perfectly. "
        "Preserve exactly: garment color, cut, proportions, hood shape, zipper and zipper pull, pockets, "
        "cuffs and hem elastic, stitching, seams, any print or label. Do not redesign or recolor the item. "
        "Keep the color true to the reference. "
        "Avoid: CGI or 3D render look, plastic or overly smooth fabric, glossy airbrushed look, perfect symmetry, "
        "floating hanger, hanger hook not touching the bar, warped rack, extra fingers or people, random text, "
        "logos, watermarks, harsh HDR, oversaturated colors, empty flat gray background."
    )


async def generate_on_hanger(
    product_image: str,
    placement: str = "hanger",
    note: str = "",
    context_image: str = "",
) -> bytes:
    """Фото вещи → кадр без человека: вешалка, рейл, стойка, кровать или пол."""
    if placement == "stand":
        prompt = _stand_prompt()
    elif placement == "hanger":
        scene = random.choice(_HANGER_SCENES)
        prompt = (
            "Lifestyle marketplace photo in a cozy bright room, as if the garment was just taken "
            "from the wardrobe to try on. "
            f"It hangs on {scene}. "
            "Laconic interior: pale walls, soft window daylight, light floor, almost no furniture, "
            "no clutter, no text, no logos, no shopping bags, no other clothes. "
            "Front view, the whole garment in frame with space around it, natural drape, the garment is the hero. "
            "The rail or wardrobe is complete and stands naturally. "
            f"{_KEEP}"
        )
    else:
        prompt = _PACKSHOT_PROMPTS.get(placement, _PACKSHOT_PROMPTS["bed"])
    if note:
        prompt += f" User correction: {note}. Keep every other garment detail unchanged."
    inputs = {
        "product_image": product_image,
        "prompt": prompt,
        "aspect_ratio": "4:5" if placement == "rail" else "3:4",
        **_FIDELITY,
    }
    if context_image:
        inputs["image_context"] = context_image
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=120.0)) as client:
        return await _run(client, inputs, "packshot")


async def generate_tryon(product_image: str, model_image: str, note: str = "") -> bytes:
    """Фото человека + фото вещи. Поза и фон человека сохраняются."""
    prompt = (
        "Copy the garment from the product photo exactly: same color, print, fabric, "
        "stitching, pockets, buttons, zippers, logos, length and shape. "
        "Do not add details that are not in the product photo. Do not recolor it."
    )
    if note:
        prompt += f" User correction: {note}. Keep every other garment detail unchanged."
    inputs = {
        "product_image": product_image,
        "model_image": model_image,
        "prompt": prompt,
        **_FIDELITY,
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=120.0)) as client:
        return await _run(client, inputs, "tryon-max")
