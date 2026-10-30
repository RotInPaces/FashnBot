"""Telegram-бот примерки одежды через FASHN."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import uuid
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)
from dotenv import load_dotenv

import fashn

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

def _balances_path() -> Path:
    data_dir = os.getenv("DATA_DIR", "").strip()
    if data_dir:
        path = Path(data_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path / "balances.json"
    return Path(__file__).with_name("balances.json")


def _examples_dir() -> Path:
    raw = os.getenv("EXAMPLES_DIR", "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).with_name("examples")


BALANCES_PATH = _balances_path()
EXAMPLES_DIR = _examples_dir()
EXAMPLE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
_balance_lock = asyncio.Lock()
_jobs: dict[str, dict] = {}

ORIGINS: dict[str, str] = {
    "ru": "Русская",
    "eu": "Европейская",
    "asia": "Азиатская",
    "latam": "Латиноамериканская",
    "mena": "Ближневосточная",
    "af": "Африканская",
}
INTERIORS: dict[str, str] = {
    "mirror": "Зеркало, фото для отзыва",
    "room": "Повседневная комната",
    "room_styled": "Стильная комната",
    "street": "Улица",
    "cafe": "Кафе",
    "fitting": "Примерочная",
    "studio": "Студия",
}
SHOTS: dict[str, str] = {
    "self": "В зеркале",
    "friend": "Снимает подруга",
    "partner": "Снимает парень",
}
GENDERS: dict[str, str] = {
    "f": "Женский",
    "m": "Мужской",
}
LOOKS: dict[str, str] = {
    "plain": "Обычная",
    "groomed": "Ухоженная",
}
FRAMES: dict[str, str] = {
    "full": "Стоит ровно",
    "relax": "Свободно стоит",
    "turn": "Лёгкий поворот",
    "walk": "На ходу",
    "knee": "По колено",
    "waist": "По пояс",
    "sit": "Сидит",
    "custom": "Своя поза",
}
ORIGIN_LOOK: dict[str, str] = {
    "ru": "Russian, natural Slavic features",
    "eu": "European, natural features",
    "asia": "East Asian, natural features",
    "latam": "Latin American, natural features",
    "mena": "Middle Eastern, natural features",
    "af": "African, natural features",
}
FRAME_PROMPTS: dict[str, str] = {
    "full": "full-body photo, standing naturally, looking at the camera, clothes fully visible",
    "relax": "full-body photo, relaxed stance with weight on one leg, arms natural, looking at the camera, as if showing new clothes",
    "turn": "full-body three-quarter view, head to shoes in frame, same garment visible from the side, looking toward the camera",
    "walk": "casual mid-step walk, clothes fully visible, natural arms, looking ahead",
    "knee": "photo from head to knees, standing naturally, looking at the camera",
    "waist": "waist-up photo, looking at the camera, garment details visible",
    "sit": "sitting naturally, garment visible, looking at the camera",
}
FRAME_RATIOS: dict[str, str] = {
    "full": "2:3",
    "relax": "2:3",
    "turn": "2:3",
    "walk": "2:3",
    "knee": "3:4",
    "waist": "3:4",
    "sit": "3:4",
    "custom": "2:3",
}
INTERIOR_PROMPTS: dict[str, str] = {
    "mirror": "in a home hallway by a mirror, soft daylight",
    "room": "in a lived-in apartment room with ordinary furniture, everyday home, soft daylight",
    "room_styled": "in a neatly designed apartment with quality furniture, calm neutral colors and considered styling, soft daylight",
    "street": "on an ordinary city street, daylight",
    "cafe": "in a simple cafe",
    "fitting": "in a clothing-store fitting room",
    "studio": "in a plain light studio with even soft light",
}
SEASONS: dict[str, str] = {
    "spring": "Весна",
    "summer": "Лето",
    "autumn": "Осень",
    "winter": "Зима",
}
SEASON_PROMPTS: dict[str, str] = {
    "spring": "early spring, mild weather, no snow, trees just budding",
    "summer": "warm summer, green trees, bright daylight, no snow",
    "autumn": "autumn, fallen leaves, cool daylight, no snow",
    "winter": "winter, snow on the ground, cold daylight",
}
SHOT_PROMPTS: dict[str, str] = {
    "self": "casual smartphone mirror selfie, phone visible in the mirror, realistic product review photo",
    "friend": "a female friend is taking the photo with a smartphone, the subject is not holding the phone, candid review snapshot",
    "partner": "a boyfriend is taking the photo with a smartphone, the subject is not holding the phone, candid review snapshot",
}
PLACEMENTS: dict[str, str] = {
    "hanger": "На вешалке",
    "rail": "На рейле",
    "stand": "На напольной вешалке",
    "bed": "На кровати",
    "white": "На белом фоне",
    "floor": "На полу",
}
PACKSHOT_REFS: dict[str, str] = {
    "rail": "rail",
    "floor": "floor",
}
SET_SIZE = 5
SET3_VIEWS: tuple[str, ...] = (
    "full-body front view, facing the camera, arms relaxed, the whole garment visible",
    "full-body back view, standing straight, the whole garment visible from behind",
    "full-body strict left profile, body and face turned 90 degrees, looking perpendicular to the camera, not at the camera, garment fully visible from the left side",
    "full-body strict right profile, body and face turned 90 degrees, looking perpendicular to the camera, not at the camera, garment fully visible from the right side",
    "full-body three-quarter diagonal view, garment visible from the front and one side",
)
SET_POSES: tuple[str, ...] = (
    "full-body front view, standing naturally, arms relaxed, looking at the camera, clothes fully visible",
    "full-body three-quarter view, looking toward the camera, clothes fully visible",
    "full-body side view, same garment visible from the side, looking toward the camera",
    "casual mid-step walk, clothes fully visible, natural arms",
    "relaxed full-body stance, weight on one leg, clothes fully visible, looking at the camera",
)
SETS: dict[str, dict[str, str]] = {
    "1": {
        "title": "Сет №1",
        "scene": "studio",
        "about": "Белая студия.",
    },
    "2": {
        "title": "Сет №2",
        "scene": "interior",
        "about": "Интерьер из настроек.",
    },
    "3": {
        "title": "Сет №3",
        "scene": "white",
        "about": "Одежда со всех сторон на белом фоне.",
    },
    "4": {
        "title": "Сет №4",
        "scene": "locked",
        "about": "",
    },
    "5": {
        "title": "Сет №5",
        "scene": "locked",
        "about": "",
    },
}


class Flow(StatesGroup):
    garment = State()
    person = State()
    tryon_garment = State()
    hanger = State()
    regen = State()
    pose = State()
    photoset = State()
    extra = State()


_last_job: dict[int, dict] = {}


def remember_job(user_id: int, payload: dict) -> str:
    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = payload
    _last_job[user_id] = payload
    while len(_jobs) > 100:
        _jobs.pop(next(iter(_jobs)))
    return job_id
# Своя поза: data URI только в памяти, на диск не пишем.
_pose_ref: dict[int, str] = {}


# Рубли и токены: 25 ₽ за токен. В кнопке старая цена — 120% от этой.
PACKAGES: tuple[tuple[int, int], ...] = ((250, 10), (875, 35), (1500, 60), (2500, 100))


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"В .env не задан {name}")
    return value


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} должен быть целым числом") from exc


BOT_TOKEN = _required("BOT_TOKEN")
_required("FASHN_API_KEY")
START_BALANCE = max(0, _env_int("START_BALANCE", 3))
GENERATION_COST = _env_int("GENERATION_COST", 1)
if GENERATION_COST < 1:
    raise SystemExit("GENERATION_COST должен быть не меньше 1")


def _admin_ids() -> set[int]:
    ids: set[int] = set()
    for part in os.getenv("ADMIN_IDS", "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError as exc:
            raise SystemExit("ADMIN_IDS: числовые Telegram ID через запятую") from exc
    return ids


ADMIN_IDS = _admin_ids()


def _support_id() -> int:
    raw = os.getenv("SUPPORT_ID", "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError as exc:
            raise SystemExit("SUPPORT_ID должен быть числовым Telegram ID") from exc
        if value < 1:
            raise SystemExit("SUPPORT_ID должен быть числовым Telegram ID")
        return value
    for part in os.getenv("ADMIN_IDS", "").split(","):
        part = part.strip()
        if not part:
            continue
        return int(part)
    return 0


SUPPORT_ID = _support_id()
PRIVACY_URL = "https://telegra.ph/Politika-konfidencialnosti-10-30-73"
TERMS_URL = "https://telegra.ph/Polzovatelskoe-soglashenie-10-30-40"
router = Router()


def _load_balances() -> dict:
    if not BALANCES_PATH.exists():
        return {}
    try:
        data = json.loads(BALANCES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Не удалось прочитать файл баланса: %s", type(exc).__name__)
        raise
    if not isinstance(data, dict):
        raise ValueError("balances root must be an object")
    return data


def _save_balances(data: dict) -> None:
    tmp = BALANCES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(BALANCES_PATH)


def _blank_user(balance: int) -> dict:
    return {
        "balance": balance,
        "origin": "ru",
        "interior": "mirror",
        "gender": "f",
        "frame": "full",
        "shot": "self",
        "season": "summer",
        "look": "plain",
        "extra": "",
    }


async def ensure_user(user_id: int) -> dict:
    async with _balance_lock:
        data = _load_balances()
        key = str(user_id)
        if key not in data or not isinstance(data[key], dict):
            data[key] = _blank_user(START_BALANCE)
            _save_balances(data)
        user = data[key]
        changed = False
        defaults = {
            "balance": START_BALANCE,
            "origin": "ru",
            "interior": "mirror",
            "gender": "f",
            "frame": "full",
            "shot": "self",
            "season": "summer",
            "look": "plain",
            "extra": "",
        }
        for field, default in defaults.items():
            if field not in user:
                user[field] = default
                changed = True
        if changed:
            _save_balances(data)
        return dict(user)


async def update_prefs(
    user_id: int,
    *,
    origin: str | None = None,
    interior: str | None = None,
    gender: str | None = None,
    frame: str | None = None,
    shot: str | None = None,
    season: str | None = None,
    look: str | None = None,
    extra: str | None = None,
) -> dict:
    async with _balance_lock:
        data = _load_balances()
        key = str(user_id)
        user = data.get(key)
        if not isinstance(user, dict):
            user = _blank_user(START_BALANCE)
        if origin in ORIGINS:
            user["origin"] = origin
        if interior in INTERIORS:
            user["interior"] = interior
        if gender in GENDERS:
            user["gender"] = gender
        if frame in FRAMES:
            user["frame"] = frame
        if shot in SHOTS:
            user["shot"] = shot
        if season in SEASONS:
            user["season"] = season
        if look in LOOKS:
            user["look"] = look
        if extra is not None:
            user["extra"] = " ".join(extra.split())[:300]
        data[key] = user
        _save_balances(data)
        return dict(user)


def photoset_cost() -> int:
    return SET_SIZE * GENERATION_COST


FACE_LOCK_EXTRA = 15


def set_price(face_lock: bool, made: int | None = None) -> int:
    count = SET_SIZE if made is None else made
    extra = FACE_LOCK_EXTRA * count // SET_SIZE if face_lock else 0
    return count * GENERATION_COST + extra


async def spend(user_id: int, amount: int | None = None) -> int | None:
    cost = GENERATION_COST if amount is None else amount
    async with _balance_lock:
        data = _load_balances()
        key = str(user_id)
        user = data.get(key)
        if not isinstance(user, dict):
            return None
        balance = int(user.get("balance", 0))
        if cost < 1 or balance < cost:
            return None
        user["balance"] = balance - cost
        data[key] = user
        _save_balances(data)
        return int(user["balance"])


async def add_credits(user_id: int, amount: int) -> int:
    """Пополнение счёта. Сюда позже подключается оплата."""
    async with _balance_lock:
        data = _load_balances()
        key = str(user_id)
        user = data.get(key)
        if not isinstance(user, dict):
            user = _blank_user(0)
        user["balance"] = max(0, int(user.get("balance", 0)) + amount)
        data[key] = user
        _save_balances(data)
        return int(user["balance"])


def appearance_prompt(user: dict) -> str:
    gender = "woman" if user.get("gender") != "m" else "man"
    features = ORIGIN_LOOK.get(str(user.get("origin")), ORIGIN_LOOK["ru"])
    if user.get("look") == "groomed":
        styling = (
            f"well-groomed young {gender}, {features}, styled tidy hair, fresh rested face, "
            "polished fashion-model appearance, realistic skin"
        )
    else:
        styling = (
            f"ordinary young {gender}, {features}, clean brushed hair, fresh rested face, "
            "simple everyday look, not a fashion model"
        )
    return styling + ", not tired, not exhausted, no messy or unkempt hair"


POSE_REFERENCE = (
    "Match only the body pose from the pose reference: stance, arms, legs, head angle and crop. "
    "Do not copy that reference person's face, hair, identity, clothes, colors, lighting or room"
)


def prompt_for(user: dict, *, pose_photo: bool = False) -> str:
    interior = INTERIOR_PROMPTS.get(str(user.get("interior")), INTERIOR_PROMPTS["mirror"])
    if pose_photo:
        frame = POSE_REFERENCE
    else:
        frame = FRAME_PROMPTS.get(str(user.get("frame")), FRAME_PROMPTS["full"])
    shot = SHOT_PROMPTS.get(str(user.get("shot")), SHOT_PROMPTS["self"])
    if user.get("shot", "self") == "self" and user.get("interior") != "mirror":
        shot = "casual smartphone photo, realistic product review photo"
    season = ""
    if user.get("interior") == "street":
        season = SEASON_PROMPTS.get(str(user.get("season")), SEASON_PROMPTS["summer"]) + ". "
    return (
        "Copy the garment from the product photo exactly: same color, print, fabric, "
        "stitching, pockets, buttons, zippers, logos, length and shape. "
        "Do not add, remove or recolor any detail. Do not invent another jacket or an extra layer. "
        f"The person is {appearance_prompt(user)}. "
        f"{frame}. {interior}, {season}{shot}."
        f"{extra_clause(user)}"
    )


def aspect_for(user: dict) -> str:
    return FRAME_RATIOS.get(str(user.get("frame")), FRAME_RATIOS["full"])


def set_folder(set_id: str) -> Path:
    return EXAMPLES_DIR / "sets" / f"set-{set_id}"


def find_example(folder: Path, name: str) -> Path | None:
    for suffix in EXAMPLE_SUFFIXES:
        path = folder / f"{name}{suffix}"
        if path.is_file():
            return path
    return None


def packshot_context(placement: str) -> str:
    name = PACKSHOT_REFS.get(placement)
    if not name:
        return ""
    path = find_example(EXAMPLES_DIR, name)
    if path is None:
        return ""
    return file_data_uri(path)


def choose_pose_files(set_id: str) -> list[Path | None]:
    """Сеты 1, 2 и 4 берут poses/1…5. Сет 3 без образцов. Сет 5 выбирает 5 случайных."""
    if set_id == "3":
        return [None] * SET_SIZE
    folder = set_folder(set_id) / "poses"
    if set_id == "5":
        found = [
            path
            for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in EXAMPLE_SUFFIXES
        ] if folder.is_dir() else []
        if len(found) > SET_SIZE:
            found = random.sample(found, SET_SIZE)
        else:
            found.sort(key=lambda path: path.name)
        slots: list[Path | None] = list(found)
        while len(slots) < SET_SIZE:
            slots.append(None)
        return slots
    return [find_example(folder, str(number)) for number in range(1, SET_SIZE + 1)]


def file_data_uri(path: Path) -> str:
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "image/jpeg")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def set_heading(set_id: str) -> str:
    info = SETS[set_id]
    if info["about"]:
        return f"{info['title']}. {info['about']}"
    return info["title"]


def photoset_prompt(user: dict, set_id: str, index: int, *, has_room: bool, has_pose: bool, has_model: bool) -> str:
    info = SETS[set_id]
    if set_id == "3":
        pose = SET3_VIEWS[index]
        scene = "on a seamless pure white background, even soft light, no furniture, no room"
        shot = "clean catalog photo, not a mirror selfie"
        mood = " Calm face, not sad, not frowning, not tired."
        extra = (
            " On the back and side views continue the same garment. "
            "Do not invent prints, pockets, seams, logos, buttons or an extra layer that are not in the product photo."
        )
    else:
        pose = POSE_REFERENCE if has_pose else SET_POSES[index]
        mood = ""
        extra = ""
        if info["scene"] == "studio":
            scene = "in a plain white seamless photo studio, even soft light, no furniture"
            shot = "clean studio catalog photo, not a mirror selfie"
        elif info["scene"] == "interior":
            scene = INTERIOR_PROMPTS.get(str(user.get("interior")), INTERIOR_PROMPTS["mirror"])
            shot = SHOT_PROMPTS.get(str(user.get("shot")), SHOT_PROMPTS["self"])
            if user.get("shot", "self") == "self" and user.get("interior") != "mirror":
                shot = "casual smartphone photo, realistic product review photo"
            if user.get("interior") == "street":
                season = SEASON_PROMPTS.get(str(user.get("season")), SEASON_PROMPTS["summer"])
                scene = f"{scene}, {season}"
        else:
            scene = "in one consistent place for the whole photoshoot"
            shot = "natural clothing photo from the same photoshoot"
    room = ""
    if has_room and set_id != "3":
        room = " Keep the same room as the background reference. Do not change the location or furniture."
    identity = ""
    if has_model:
        identity = (
            " The face reference is this exact person: keep the same face and hair. "
            "Do not copy that photo's clothes, pose or background."
        )
    return (
        "Copy the garment from the product photo exactly: same color, print, fabric, "
        "stitching, pockets, buttons, zippers, logos, length and shape. "
        "Do not add, remove or recolor any detail. Do not invent another jacket or an extra layer. "
        f"{extra}"
        f"The person is {appearance_prompt(user)}.{mood}{identity} "
        f"{pose}. {scene}, {shot}.{room}"
        f"{extra_clause(user)}"
    )


def confirm_text(heading: str, detail: str, balance: int, cost: int, ask: str = "Пришлите одно фото одежды.") -> str:
    return (
        f"{heading}\n\n"
        f"Сейчас: {detail}\n"
        f"Стоимость: {token_count(cost)}. На счёте: {token_count(balance)}.\n\n"
        f"{DISCLAIMER}\n\n"
        f"{ask}"
    )


def pose_photo(user_id: int, user: dict) -> str:
    if user.get("frame") != "custom":
        return ""
    return _pose_ref.get(user_id, "")


def prefs_line(user: dict, user_id: int | None = None) -> str:
    gender = GENDERS.get(str(user.get("gender")), GENDERS["f"])
    look = LOOKS.get(str(user.get("look")), LOOKS["plain"])
    frame = FRAMES.get(str(user.get("frame")), FRAMES["full"])
    if user.get("frame") == "custom" and user_id is not None and user_id not in _pose_ref:
        frame = "Своя поза, фото не прислано"
    origin = ORIGINS.get(str(user.get("origin")), ORIGINS["ru"])
    interior = INTERIORS.get(str(user.get("interior")), INTERIORS["mirror"])
    shot = SHOTS.get(str(user.get("shot")), SHOTS["self"])
    line = f"{gender}. {look}. {frame}. {shot}.\n{origin}. {interior}."
    if user.get("interior") == "street":
        season = SEASONS.get(str(user.get("season")), SEASONS["summer"])
        line += f" {season}."
    line += f"\n{extra_line(user)}"
    return line


def user_extra(user: dict) -> str:
    return " ".join(str(user.get("extra") or "").split())[:300]


def extra_line(user: dict) -> str:
    text = user_extra(user)
    return f"Свой промпт: {text if text else 'нет'}"


def extra_clause(user: dict) -> str:
    text = user_extra(user)
    if not text:
        return ""
    return f" Additional request: {text}."


def token_count(amount: int) -> str:
    number = abs(int(amount))
    if number % 10 == 1 and number % 100 != 11:
        word = "токен"
    elif 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        word = "токена"
    else:
        word = "токенов"
    return f"{amount} {word}"


DISCLAIMER = (
    "Генерация иногда ошибается: вещь, лицо или фон могут получиться неточно. "
    "Вы генерируете на свой страх и риск. Мы стараемся улучшить сервис "
    "и делаем всё возможное, чтобы вам было комфортно пользоваться ботом."
)
REF_TEXT = (
    "Так лучше снимать одежду для генерации.\n\n"
    "Фото желательно в хорошем качестве, чтобы детали были ярко и чётко видны. "
    "Иначе одежда на готовом изображении может отличаться от референса."
)
POSE_ASK = (
    "Пришлите одно фото с нужной позой.\n\n"
    "Модель возьмёт с него только положение тела: стойку, руки, ноги и ракурс. "
    "Лицо, волосы, одежду и комнату с этого фото она не копирует. "
    "Внешность и интерьер останутся из настроек."
)


def main_text(user: dict) -> str:
    return (
        "Бот собирает фото одежды для маркетплейса и примерки вещей, "
        "как они будут смотреться на модели.\n\n"
        "Можно сгенерировать человека в вашей вещи, собрать фотосет из пяти кадров, "
        "примерить вещь на своё фото или снять её на вешалке, рейле, кровати, белом фоне или полу.\n\n"
        "По умолчанию это обычная русская девушка, которая купила вещь "
        "и сняла себя в зеркале.\n\n"
        f"{DISCLAIMER}\n\n"
        f"Одна генерация — {token_count(GENERATION_COST)}. На счёте: {token_count(user['balance'])}.\n\n"
        "Выберите действие кнопкой."
    )


def main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Генерация по фото одежды", callback_data="clothes")],
            [InlineKeyboardButton(text="Примерка на своё фото", callback_data="tryon")],
            [InlineKeyboardButton(text="Фото вещи", callback_data="product")],
            [
                InlineKeyboardButton(text="Баланс", callback_data="balance"),
                InlineKeyboardButton(text="Пополнить", callback_data="topup"),
            ],
            [InlineKeyboardButton(text="Настройки", callback_data="settings")],
            [InlineKeyboardButton(text="Политика конфиденциальности", url=PRIVACY_URL)],
            [InlineKeyboardButton(text="Пользовательское соглашение", url=TERMS_URL)],
            *support_row(),
        ]
    )


def support_row() -> list[list[InlineKeyboardButton]]:
    if SUPPORT_ID < 1:
        return []
    return [[InlineKeyboardButton(text="Обращение в тех.поддержку", url=f"tg://user?id={SUPPORT_ID}")]]


def clothes_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Пол", callback_data="pick:gender"),
                InlineKeyboardButton(text="Образ", callback_data="pick:look"),
            ],
            [InlineKeyboardButton(text="Поза", callback_data="pick:frame")],
            [
                InlineKeyboardButton(text="Происхождение", callback_data="pick:origin"),
                InlineKeyboardButton(text="Интерьер", callback_data="pick:interior"),
            ],
            [
                InlineKeyboardButton(text="Съёмка", callback_data="pick:shot"),
                InlineKeyboardButton(text="Сезон", callback_data="pick:season"),
            ],
            [InlineKeyboardButton(text="Добавить детали к запросу", callback_data="extra")],
            [InlineKeyboardButton(text="К генерации", callback_data="ready")],
            [InlineKeyboardButton(text="Фотосет из 5 фото", callback_data="photosets")],
            [InlineKeyboardButton(text="В меню", callback_data="menu")],
        ]
    )


def choice_kb(options: dict[str, str], current: str, kind: str) -> InlineKeyboardMarkup:
    rows = []
    for key, label in options.items():
        mark = "✓ " if key == current else ""
        rows.append([InlineKeyboardButton(text=f"{mark}{label}", callback_data=f"set:{kind}:{key}")])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="В меню", callback_data="menu")]]
    )


def extra_kb(has_text: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_text:
        rows.append([InlineKeyboardButton(text="Убрать свой промпт", callback_data="extra_clear")])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def pose_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="back")]]
    )


def result_kb(mode: str, job_id: str = "") -> InlineKeyboardMarkup:
    if mode.startswith("place:") or mode.startswith("pmake:"):
        repeat = mode
    else:
        repeat = {"tryon": "tryon"}.get(mode, "ready")
    rows = [
        [InlineKeyboardButton(text="Ещё раз", callback_data=repeat)],
        [InlineKeyboardButton(text="Перегенерировать", callback_data=f"regen:{job_id}" if job_id else "regen")],
    ]
    if mode != "clothes":
        rows.append([InlineKeyboardButton(text="Генерация по фото одежды", callback_data="clothes")])
    if mode == "clothes":
        rows.append([InlineKeyboardButton(text="Настройки", callback_data="settings")])
    rows.append([InlineKeyboardButton(text="Пополнить", callback_data="topup")])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def package_label(rubles: int, credits: int) -> str:
    return f"🔥 {rubles} ₽ — {token_count(credits)} 💎 🔥"


def package_html(rubles: int, credits: int) -> str:
    shown = rubles * 6 // 5
    return f"🔥 <s>{shown}</s> {rubles} ₽ — {token_count(credits)} 💎 🔥"


def topup_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=package_label(rubles, credits), callback_data=f"pay:{rubles}")]
        for rubles, credits in PACKAGES
    ]
    rows.append([InlineKeyboardButton(text="Назад", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show(message: Message, text: str, markup: InlineKeyboardMarkup, *, edit: bool) -> None:
    try:
        if edit:
            await message.edit_text(text, reply_markup=markup)
            return
        await message.answer(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        if edit:
            await message.answer(text, reply_markup=markup)
            return
        logger.warning("Telegram reject: %s", type(exc).__name__)


async def show_main(message: Message, user_id: int, *, edit: bool) -> None:
    user = await ensure_user(user_id)
    await show(message, main_text(user), main_kb(), edit=edit)


def ready_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Примеры генерации", callback_data="examples")],
            [InlineKeyboardButton(text="Какие фото слать", callback_data="refs")],
            [InlineKeyboardButton(text="Изменить настройки", callback_data="clothes")],
            [InlineKeyboardButton(text="В меню", callback_data="menu")],
        ]
    )


def _named_example(name: str) -> Path | None:
    return next(
        (
            EXAMPLES_DIR / f"{name}{suffix}"
            for suffix in EXAMPLE_SUFFIXES
            if (EXAMPLES_DIR / f"{name}{suffix}").is_file()
        ),
        None,
    )


def example_files() -> list[Path] | None:
    found: list[Path] = []
    number = 1
    while True:
        before = _named_example(f"before-{number}")
        if before is None:
            break
        after = _named_example(f"after-{number}")
        if after is None and number == 4:
            after = _named_example("after-5")
        if after is None:
            break
        found.append(before)
        found.append(after)
        number += 1
    if len(found) < 2:
        return None
    return found


async def show_clothes(message: Message, user_id: int, *, edit: bool) -> None:
    user = await ensure_user(user_id)
    text = (
        "Настройте кадр и нажмите «К генерации».\n\n"
        f"Сейчас: {prefs_line(user, user_id)}"
    )
    await show(message, text, clothes_kb(), edit=edit)


async def show_ready(message: Message, user_id: int, *, edit: bool) -> None:
    user = await ensure_user(user_id)
    pose_note = ""
    if user.get("frame") == "custom" and user_id in _pose_ref:
        pose_note = "Поза: ваше фото. Берём только стойку.\n"
    text = (
        "Настройки выбраны.\n\n"
        f"Сейчас: {prefs_line(user, user_id)}\n"
        f"{pose_note}"
        f"Стоимость: {token_count(GENERATION_COST)}. На счёте: {token_count(user['balance'])}.\n\n"
        f"{DISCLAIMER}\n\n"
        "Пришлите одно фото одежды. Лучше на ровном фоне."
    )
    await show(message, text, ready_kb(), edit=edit)


async def show_settings(message: Message, user_id: int, *, edit: bool) -> None:
    user = await ensure_user(user_id)
    text = (
        "Настройки генерации по фото одежды.\n\n"
        f"Сейчас: {prefs_line(user, user_id)}\n\n"
        "На примерку по своему фото пол, образ, поза, съёмка, происхождение и интерьер не влияют: "
        "там сохраняются лицо, поза и фон снимка."
    )
    await show(message, text, clothes_kb(), edit=edit)


async def photo_to_data_uri(bot: Bot, message: Message) -> str | None:
    if not message.photo:
        return None
    downloaded = await bot.download(message.photo[-1])
    if downloaded is None:
        return None
    raw = downloaded.read()
    if not raw:
        return None
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


async def _clear_if_same(state: FSMContext, started: str | None, token: str) -> None:
    if await state.get_state() == started and (await state.get_data()).get("run_token") == token:
        await state.clear()


async def run_generation(message: Message, state: FSMContext, job, mode: str, pending: dict | None = None) -> None:
    user_id = message.from_user.id if message.from_user else 0
    started = await state.get_state()
    token = uuid.uuid4().hex
    await state.update_data(run_token=token)
    user = await ensure_user(user_id)
    if int(user["balance"]) < GENERATION_COST:
        await message.answer(
            f"Недостаточно токенов. Нужно {token_count(GENERATION_COST)}, на счёте {token_count(user['balance'])}.",
            reply_markup=main_kb(),
        )
        await _clear_if_same(state, started, token)
        return

    balance = await spend(user_id)
    if balance is None:
        await message.answer(
            f"Недостаточно токенов. Нужно {token_count(GENERATION_COST)}, на счёте {token_count(int(user['balance']))}.",
            reply_markup=main_kb(),
        )
        await _clear_if_same(state, started, token)
        return

    job_id = remember_job(user_id, pending) if pending else ""
    status = await message.answer("Генерирую фото. Обычно это занимает до минуты.")
    try:
        image = await job()
    except fashn.FashnError as exc:
        logger.error("Generation failed: %s", exc.name)
        returned = await add_credits(user_id, GENERATION_COST)
        await status.edit_text(fashn.user_message(exc))
        await message.answer(f"Токены не списаны. На счёте: {token_count(returned)}.", reply_markup=main_kb())
        await _clear_if_same(state, started, token)
        return
    except Exception:
        logger.exception("Generation crashed")
        returned = await add_credits(user_id, GENERATION_COST)
        await status.edit_text("Не получилось сгенерировать фото. Попробуйте ещё раз.")
        await message.answer(f"Токены не списаны. На счёте: {token_count(returned)}.", reply_markup=main_kb())
        await _clear_if_same(state, started, token)
        return

    if not image:
        returned = await add_credits(user_id, GENERATION_COST)
        await status.edit_text("Сервис не вернул изображение.")
        await message.answer(f"Токены не списаны. На счёте: {token_count(returned)}.", reply_markup=main_kb())
        await _clear_if_same(state, started, token)
        return

    try:
        await status.delete()
    except TelegramBadRequest:
        pass
    try:
        await message.answer_photo(
            BufferedInputFile(image, filename="result.png"),
            caption="Готово.",
        )
        fresh = await ensure_user(user_id)
        await message.answer(
            f"На счёте: {token_count(fresh['balance'])}.\nОдна генерация — {token_count(GENERATION_COST)}.",
            reply_markup=result_kb(mode, job_id),
        )
    except Exception:
        logger.exception("Failed to send generated photo")
        returned = await add_credits(user_id, GENERATION_COST)
        await message.answer(
            f"Не удалось отправить фото. Токены возвращены. На счёте: {token_count(returned)}.",
            reply_markup=main_kb(),
        )
    await _clear_if_same(state, started, token)


def sets_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=info["title"], callback_data=f"pset:{set_id}")]
        for set_id, info in SETS.items()
    ]
    rows.append([InlineKeyboardButton(text="Назад", callback_data="clothes")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def make_set_kb(set_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Сгенерировать этот сет", callback_data=f"pmake:{set_id}")],
            [InlineKeyboardButton(text="С сохранением лица (+15 токенов)", callback_data=f"pmake:{set_id}:face")],
            [InlineKeyboardButton(text="К списку сетов", callback_data="photosets")],
        ]
    )


async def show_sets(message: Message, user_id: int, *, edit: bool) -> None:
    user = await ensure_user(user_id)
    text = (
        "Фотосет — 5 кадров в вашей одежде.\n\n"
        "Сет №1 — белая студия.\n"
        "Сет №2 — интерьер из настроек.\n"
        "Сет №3 — одежда со всех сторон на белом фоне.\n"
        "Сет №4.\n"
        "Сет №5.\n"
        "В сетах №1, №2 и №4 стойка берётся из образцов поз. "
        "В сете №5 пять поз выбираются случайно.\n"
        "Сохранение лица на всех кадрах включается отдельно, это плюс 15 токенов.\n\n"
        f"Сет стоит {token_count(photoset_cost())}. На счёте: {token_count(user['balance'])}."
    )
    await show(message, text, sets_kb(), edit=edit)


async def send_set_examples(message: Message, set_id: str) -> None:
    folder = set_folder(set_id)
    files = [find_example(folder, str(number)) for number in range(1, SET_SIZE + 1)]
    found = [path for path in files if path is not None]
    caption = (
        f"{set_heading(set_id)}\n\n"
        "Готовый результат может немного отличаться от примеров."
    )
    if not found:
        return
    if len(found) == 1:
        await message.answer_photo(FSInputFile(found[0]), caption=caption)
        return
    media = [
        InputMediaPhoto(media=FSInputFile(path), caption=caption if index == 0 else None)
        for index, path in enumerate(found)
    ]
    await message.answer_media_group(media)


async def run_photoset(
    message: Message,
    state: FSMContext,
    product_uri: str,
    set_id: str,
    note: str = "",
    face_lock: bool = False,
) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if set_id not in SETS:
        await message.answer("Выберите сет кнопкой.", reply_markup=sets_kb())
        return
    user = await ensure_user(user_id)
    cost = set_price(face_lock)
    if int(user["balance"]) < cost:
        await message.answer(
            f"Недостаточно токенов. Сет стоит {token_count(cost)}, на счёте {token_count(user['balance'])}.",
            reply_markup=main_kb(),
        )
        return

    started = await state.get_state()
    token = uuid.uuid4().hex
    await state.update_data(run_token=token)
    balance = await spend(user_id, cost)
    if balance is None:
        await message.answer(
            f"Недостаточно токенов. Сет стоит {token_count(cost)}, на счёте {token_count(int(user['balance']))}.",
            reply_markup=main_kb(),
        )
        await _clear_if_same(state, started, token)
        return

    folder = set_folder(set_id)
    pose_files = choose_pose_files(set_id)
    room_file = None if SETS[set_id]["scene"] in {"interior", "white"} else find_example(folder, "room")
    room_uri = file_data_uri(room_file) if room_file is not None else ""

    images: list[bytes] = []
    locked_room = room_uri
    face_uri = ""
    error_text = ""
    try:
        status = await message.answer(f"Генерирую фото 1 из {SET_SIZE}.")
        for index in range(SET_SIZE):
            if index:
                try:
                    await status.edit_text(f"Генерирую фото {index + 1} из {SET_SIZE}.")
                except TelegramBadRequest:
                    pass
            pose_file = pose_files[index]
            logger.info(
                "Photoset %s frame %s pose %s model %s",
                set_id,
                index + 1,
                pose_file.name if pose_file is not None else "none",
                "face" if face_uri else "none",
            )
            prompt = photoset_prompt(
                user,
                set_id,
                index,
                has_room=bool(locked_room),
                has_pose=pose_file is not None,
                has_model=bool(face_uri),
            )
            if note:
                prompt += f" User correction: {note}. Keep every other garment detail unchanged."
            try:
                image = await fashn.generate_from_garment(
                    product_uri,
                    prompt,
                    "2:3",
                    file_data_uri(pose_file) if pose_file is not None else "",
                    locked_room,
                    face_uri,
                )
            except fashn.FashnError as exc:
                logger.error("Photoset failed on frame %s: %s", index + 1, exc.name)
                error_text = fashn.user_message(exc)
                break
            except Exception:
                logger.exception("Photoset crashed")
                error_text = "Не получилось сгенерировать фото. Попробуйте ещё раз."
                break
            images.append(image)
            if face_lock and index == 0:
                encoded = base64.b64encode(image).decode("ascii")
                face_uri = f"data:image/png;base64,{encoded}"

        if not images:
            returned = await add_credits(user_id, cost)
            await status.edit_text(error_text or "Не получилось сгенерировать фото. Попробуйте ещё раз.")
            await message.answer(f"Токены не списаны. На счёте: {token_count(returned)}.", reply_markup=main_kb())
            await _clear_if_same(state, started, token)
            return

        made = len(images)
        charged = set_price(face_lock, made)
        if charged < cost:
            balance = await add_credits(user_id, cost - charged)
        job_id = remember_job(
            user_id,
            {
                "kind": "photoset",
                "product": product_uri,
                "set_id": set_id,
                "face_lock": face_lock,
            },
        )
        caption = f"{SETS[set_id]['title']}. Кадры 1–{made}."
        if made < SET_SIZE:
            caption += f" Сет остановился на кадре {made + 1}. Списаны токены только за готовые фото."
        try:
            await status.delete()
        except TelegramBadRequest:
            pass
        sent = False
        try:
            if made == 1:
                await message.answer_photo(BufferedInputFile(images[0], filename="set-1.png"), caption=caption)
            else:
                media = [
                    InputMediaPhoto(
                        media=BufferedInputFile(image, filename=f"set-{number}.png"),
                        caption=caption if number == 1 else None,
                    )
                    for number, image in enumerate(images, start=1)
                ]
                await message.answer_media_group(media)
            sent = True
        except Exception:
            logger.exception("Photoset album failed, sending photos separately")
            try:
                for number, image in enumerate(images, start=1):
                    await message.answer_photo(
                        BufferedInputFile(image, filename=f"set-{number}.png"),
                        caption=caption if number == 1 else None,
                    )
                sent = True
            except Exception:
                logger.exception("Failed to send photoset")
        if sent:
            fresh = await ensure_user(user_id)
            tail = f"На счёте: {token_count(fresh['balance'])}.\nСет — {token_count(set_price(face_lock))}."
            if error_text and made < SET_SIZE:
                tail = f"{error_text}\n\n{tail}"
            await message.answer(
                tail,
                reply_markup=result_kb(f"pmake:{set_id}:face" if face_lock else f"pmake:{set_id}", job_id),
            )
        else:
            returned = await add_credits(user_id, charged)
            await message.answer(
                f"Не удалось отправить фото. Токены возвращены. На счёте: {token_count(returned)}.",
                reply_markup=main_kb(),
            )
    except Exception:
        logger.exception("Photoset crashed")
        returned = await add_credits(user_id, cost)
        await message.answer(
            f"Не получилось сгенерировать фото. Токены возвращены. На счёте: {token_count(returned)}.",
            reply_markup=main_kb(),
        )
    finally:
        await _clear_if_same(state, started, token)


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    if message.from_user:
        _last_job.pop(message.from_user.id, None)
        await show_main(message, message.from_user.id, edit=False)


@router.message(Command("balance"))
async def balance_cmd(message: Message) -> None:
    if not message.from_user:
        return
    user = await ensure_user(message.from_user.id)
    await message.answer(
        f"На счёте: {token_count(user['balance'])}. Одна генерация — {token_count(GENERATION_COST)}.",
        reply_markup=main_kb(),
    )


@router.message(Command("bonus"))
async def bonus_cmd(message: Message) -> None:
    if not message.from_user:
        return
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Команда доступна только администратору.")
        return
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("Формат: /bonus <telegram_id> <количество>")
        return
    try:
        target_id = int(parts[1])
        amount = int(parts[2])
    except ValueError:
        await message.answer("ID и количество должны быть числами.")
        return
    if amount == 0:
        await message.answer("Количество не должно быть нулём.")
        return
    balance = await add_credits(target_id, amount)
    await message.answer(f"Готово. На счёте {target_id}: {token_count(balance)}.")


@router.callback_query(F.data == "menu")
async def on_menu(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if query.message and query.from_user:
        await show_main(query.message, query.from_user.id, edit=True)
    await query.answer()


@router.callback_query(F.data == "clothes")
async def on_clothes(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Flow.garment)
    if query.message and query.from_user:
        await show_clothes(query.message, query.from_user.id, edit=True)
    await query.answer()


@router.callback_query(F.data == "ready")
async def on_ready(query: CallbackQuery, state: FSMContext) -> None:
    if query.message is None or query.from_user is None:
        await query.answer()
        return
    user = await ensure_user(query.from_user.id)
    if user.get("frame") == "custom" and query.from_user.id not in _pose_ref:
        await state.update_data(pose_back="clothes")
        await state.set_state(Flow.pose)
        await show(query.message, POSE_ASK, pose_kb(), edit=True)
        await query.answer()
        return
    await state.set_state(Flow.garment)
    await show_ready(query.message, query.from_user.id, edit=True)
    await query.answer()


@router.callback_query(F.data == "photosets")
async def on_photosets(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Flow.garment)
    if query.message and query.from_user:
        await show_sets(query.message, query.from_user.id, edit=True)
    await query.answer()


@router.callback_query(F.data.startswith("pset:"))
async def on_pset(query: CallbackQuery) -> None:
    set_id = (query.data or "").split(":", 1)[1]
    if set_id not in SETS or query.message is None:
        await query.answer()
        return
    await send_set_examples(query.message, set_id)
    user = await ensure_user(query.from_user.id)
    if set_id == "5":
        pose_line = "Пять поз случайно выбираются из загруженных образцов. С каждого берётся только стойка."
    elif set_id == "3":
        pose_line = "Пять кадров на белом фоне: спереди, сзади, слева, справа и по диагонали."
    else:
        pose_line = "Поза берётся из пяти образцов. С каждого копируется только стойка."
    await query.message.answer(
        f"{set_heading(set_id)}\n\n"
        f"{pose_line} Пол, образ и происхождение остаются из настроек.\n"
        f"Стоимость: {token_count(photoset_cost())}. На счёте: {token_count(user['balance'])}.",
        reply_markup=make_set_kb(set_id),
    )
    await query.answer()


@router.callback_query(F.data.startswith("pmake:"))
async def on_pmake(query: CallbackQuery, state: FSMContext) -> None:
    parts = (query.data or "").split(":")
    set_id = parts[1] if len(parts) > 1 else ""
    face_lock = len(parts) > 2 and parts[2] == "face"
    if set_id not in SETS or query.message is None:
        await query.answer()
        return
    user = await ensure_user(query.from_user.id)
    price = set_price(face_lock)
    if int(user["balance"]) < price:
        await query.answer(
            f"Нужно {token_count(price)}, на счёте {token_count(user['balance'])}.",
            show_alert=True,
        )
        return
    await state.set_state(Flow.photoset)
    await state.update_data(set_id=set_id, face_lock=face_lock)
    if set_id == "3":
        detail = "спереди, сзади, слева, справа и по диагонали, белый фон"
    elif set_id == "5":
        detail = "пять случайных поз"
    else:
        detail = "позы из образцов сета"
    if face_lock:
        detail += ", сохранение лица"
    detail = f"{prefs_line(user, query.from_user.id)}\nСет: {detail}."
    await query.message.answer(
        confirm_text(f"{SETS[set_id]['title']}.", detail, int(user["balance"]), price),
        reply_markup=back_kb(),
    )
    await query.answer()


@router.callback_query(F.data == "examples")
async def on_examples(query: CallbackQuery) -> None:
    files = example_files()
    if query.message is None:
        await query.answer()
        return
    if files is None:
        await query.answer("Примеры ещё не загружены", show_alert=True)
        return
    caption = (
        "По порядку пары: ваше фото, результат.\n\n"
        "Готовое изображение может немного отличаться от показанного примера."
    )
    await query.message.answer_media_group(
        [
            InputMediaPhoto(media=FSInputFile(path), caption=caption if index == 0 else None)
            for index, path in enumerate(files)
        ]
    )
    await query.answer()


@router.callback_query(F.data == "refs")
async def on_refs(query: CallbackQuery) -> None:
    if query.message is None:
        await query.answer()
        return
    files: list[Path] = []
    for name in ("ref-1", "ref-2", "ref-3", "ref-4"):
        match = next(
            (
                EXAMPLES_DIR / f"{name}{suffix}"
                for suffix in EXAMPLE_SUFFIXES
                if (EXAMPLES_DIR / f"{name}{suffix}").is_file()
            ),
            None,
        )
        if match is not None:
            files.append(match)
    if not files:
        await query.message.answer(REF_TEXT + "\n\nПримеры таких фото появятся здесь позже.")
        await query.answer()
        return
    media = [
        InputMediaPhoto(media=FSInputFile(path), caption=REF_TEXT if index == 0 else None)
        for index, path in enumerate(files)
    ]
    await query.message.answer_media_group(media)
    await query.answer()


@router.callback_query(F.data == "tryon")
async def on_tryon(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Flow.person)
    if query.message:
        user = await ensure_user(query.from_user.id)
        await show(
            query.message,
            confirm_text(
                "Примерка на своё фото.",
                "лицо, поза и фон с вашего фото. Настройки модели на этот режим не влияют.\n"
                f"{extra_line(user)}",
                int(user["balance"]),
                GENERATION_COST,
                "Пришлите своё фото. Следующим сообщением попрошу фото одежды.",
            ),
            back_kb(),
            edit=True,
        )
    await query.answer()


@router.callback_query(F.data == "balance")
async def on_balance(query: CallbackQuery) -> None:
    user = await ensure_user(query.from_user.id)
    if query.message:
        await show(
            query.message,
            f"На счёте: {token_count(user['balance'])}. Одна генерация — {token_count(GENERATION_COST)}.",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Пополнить", callback_data="topup")],
                    [InlineKeyboardButton(text="Назад", callback_data="menu")],
                ]
            ),
            edit=True,
        )
    await query.answer()


def product_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"place:{key}")]
        for key, label in PLACEMENTS.items()
    ]
    rows.append([InlineKeyboardButton(text="Добавить детали к запросу", callback_data="extra:product")])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def place_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Добавить детали к запросу", callback_data="extra:place")],
            [InlineKeyboardButton(text="Назад", callback_data="product")],
        ]
    )


async def show_product(message: Message, user_id: int, *, edit: bool) -> None:
    user = await ensure_user(user_id)
    await show(
        message,
        f"Кадр вещи без человека. Куда её положить?\n\n{extra_line(user)}",
        product_kb(),
        edit=edit,
    )


@router.callback_query(F.data == "product")
async def on_product(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if query.message:
        await show_product(query.message, query.from_user.id, edit=True)
    await query.answer()


@router.callback_query(F.data.startswith("place:"))
async def on_place(query: CallbackQuery, state: FSMContext) -> None:
    placement = (query.data or "").split(":", 1)[1]
    if placement not in PLACEMENTS or query.message is None:
        await query.answer()
        return
    await state.set_state(Flow.hanger)
    await state.update_data(placement=placement)
    user = await ensure_user(query.from_user.id)
    await show(
        query.message,
        confirm_text(
            "Фото вещи.",
            f"{PLACEMENTS[placement].lower()}, без человека.\n{extra_line(user)}",
            int(user["balance"]),
            GENERATION_COST,
        ),
        place_kb(),
        edit=True,
    )
    await query.answer()


@router.callback_query(F.data == "topup")
async def on_topup(query: CallbackQuery) -> None:
    if query.message:
        offers = "\n".join(package_html(rubles, credits) for rubles, credits in PACKAGES)
        text = (
            "Пополнение через СБП.\n\n"
            f"{offers}\n\n"
            "Банк ещё не подключён. После подключения на выбранную сумму "
            "бот пришлёт QR-код, а токены появятся после оплаты."
        )
        try:
            await query.message.edit_text(text, reply_markup=topup_kb(), parse_mode="HTML")
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                await query.message.answer(text, reply_markup=topup_kb(), parse_mode="HTML")
    await query.answer()


@router.callback_query(F.data.startswith("pay:"))
async def on_pay(query: CallbackQuery) -> None:
    raw = (query.data or "").split(":", 1)[1]
    package = next(((rubles, credits) for rubles, credits in PACKAGES if str(rubles) == raw), None)
    if package is None or query.message is None:
        await query.answer()
        return
    rubles, credits = package
    await query.message.answer(
        f"{package_html(rubles, credits)}\n\n"
        f"Счёт на {rubles} ₽, это {token_count(credits)} 💎.\n\n"
        "Оплата по СБП ещё не подключена. Здесь появится QR-код.",
        parse_mode="HTML",
    )
    await query.answer()


@router.callback_query(F.data == "settings")
async def on_settings(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if query.message:
        await show_settings(query.message, query.from_user.id, edit=True)
    await query.answer()


PICK_SCREENS: dict[str, tuple[str, dict[str, str], str]] = {
    "gender": ("Кто на фото?", GENDERS, "gender"),
    "look": ("Какой образ?", LOOKS, "look"),
    "frame": ("Как поставить человека в кадре?", FRAMES, "frame"),
    "origin": ("Какое происхождение?", ORIGINS, "origin"),
    "interior": ("Где снять фото?", INTERIORS, "interior"),
    "season": ("Какое время года на улице?", SEASONS, "season"),
    "shot": ("Кто снимает?", SHOTS, "shot"),
}


@router.callback_query(F.data.startswith("pick:"))
async def on_pick(query: CallbackQuery) -> None:
    kind = (query.data or "").split(":", 1)[1]
    screen = PICK_SCREENS.get(kind)
    if screen is None or query.message is None:
        await query.answer()
        return
    title, options, field = screen
    user = await ensure_user(query.from_user.id)
    await show(query.message, title, choice_kb(options, str(user.get(field)), kind), edit=True)
    await query.answer()


@router.callback_query(F.data.startswith("set:"))
async def on_set(query: CallbackQuery, state: FSMContext) -> None:
    parts = (query.data or "").split(":")
    if len(parts) != 3:
        await query.answer()
        return
    _, kind, key = parts
    if kind == "origin":
        await update_prefs(query.from_user.id, origin=key)
    elif kind == "interior":
        await update_prefs(query.from_user.id, interior=key)
    elif kind == "gender":
        await update_prefs(query.from_user.id, gender=key)
    elif kind == "look":
        await update_prefs(query.from_user.id, look=key)
    elif kind == "frame":
        if key == "custom":
            if query.message is None:
                await query.answer()
                return
            current = await state.get_state()
            await state.update_data(pose_back="clothes" if current == Flow.garment.state else "settings")
            await state.set_state(Flow.pose)
            await show(query.message, POSE_ASK, pose_kb(), edit=True)
            await query.answer()
            return
        _pose_ref.pop(query.from_user.id, None)
        await update_prefs(query.from_user.id, frame=key)
    elif kind == "shot":
        await update_prefs(query.from_user.id, shot=key)
    elif kind == "season":
        await update_prefs(query.from_user.id, season=key)
    current = await state.get_state()
    if query.message:
        if current == Flow.garment.state:
            await show_clothes(query.message, query.from_user.id, edit=True)
        else:
            await show_settings(query.message, query.from_user.id, edit=True)
    await query.answer("Сохранено")


@router.callback_query(F.data == "back")
async def on_back(query: CallbackQuery, state: FSMContext) -> None:
    current = await state.get_state()
    if query.message:
        if current == Flow.pose.state:
            data = await state.get_data()
            if data.get("pose_back") == "clothes":
                await state.set_state(Flow.garment)
                await show_clothes(query.message, query.from_user.id, edit=True)
            else:
                await state.clear()
                await show_settings(query.message, query.from_user.id, edit=True)
        elif current == Flow.extra.state:
            await show_after_extra(query.message, state, query.from_user.id, edit=True)
        elif current == Flow.garment.state:
            await show_clothes(query.message, query.from_user.id, edit=True)
        else:
            await show_settings(query.message, query.from_user.id, edit=True)
    await query.answer()


@router.message(F.photo)
async def on_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.from_user:
        return
    if message.media_group_id:
        await message.answer("Нужно одно фото в сообщении.")
        return

    current = await state.get_state()
    if current == Flow.regen.state:
        await message.answer("Напишите текстом, что исправить. Например: аккуратные волосы, цвет как на фото.")
        return
    if current == Flow.extra.state:
        await message.answer("Напишите детали текстом или нажмите «Назад».")
        return
    uri = await photo_to_data_uri(bot, message)
    if not uri:
        await message.answer("Не удалось прочитать фото. Пришлите изображение ещё раз.")
        return

    if current == Flow.pose.state:
        _pose_ref[message.from_user.id] = uri
        await update_prefs(message.from_user.id, frame="custom")
        data = await state.get_data()
        await message.answer(
            "Поза принята. С этого фото возьмётся только положение тела. "
            "Лицо, волосы, одежду и комнату модель не копирует."
        )
        if data.get("pose_back") == "settings":
            await state.clear()
            await show_settings(message, message.from_user.id, edit=False)
        else:
            await state.set_state(Flow.garment)
            await show_clothes(message, message.from_user.id, edit=False)
        return

    if current == Flow.photoset.state:
        data = await state.get_data()
        set_id = str(data.get("set_id") or "")
        if set_id not in SETS:
            await message.answer("Сначала выберите сет.", reply_markup=sets_kb())
            return
        face_lock = bool(data.get("face_lock"))
        await run_photoset(message, state, uri, set_id, face_lock=face_lock)
        return

    if current == Flow.garment.state:
        user = await ensure_user(message.from_user.id)
        pose = pose_photo(message.from_user.id, user)
        if user.get("frame") == "custom" and not pose:
            await state.update_data(pose_back="clothes")
            await state.set_state(Flow.pose)
            await message.answer(POSE_ASK, reply_markup=pose_kb())
            return
        prompt = prompt_for(user, pose_photo=bool(pose))
        ratio = aspect_for(user)

        pending = {
            "kind": "clothes",
            "product": uri,
            "prompt": prompt,
            "aspect": ratio,
            "pose": pose,
        }

        async def job() -> bytes:
            return await fashn.generate_from_garment(uri, prompt, ratio, pose)

        await run_generation(message, state, job, "clothes", pending)
        return

    if current == Flow.hanger.state:
        data = await state.get_data()
        placement = data.get("placement")
        if placement not in PLACEMENTS:
            placement = "hanger"
        user = await ensure_user(message.from_user.id)
        own = user_extra(user)

        pending = {
            "kind": "place",
            "product": uri,
            "placement": str(placement),
            "extra": own,
        }

        async def hanger_job() -> bytes:
            return await fashn.generate_on_hanger(uri, str(placement), own, packshot_context(str(placement)))

        await run_generation(message, state, hanger_job, f"place:{placement}", pending)
        return

    if current == Flow.person.state:
        await state.update_data(person=uri)
        await state.set_state(Flow.tryon_garment)
        await message.answer("Теперь пришлите фото одежды.", reply_markup=back_kb())
        return

    if current == Flow.tryon_garment.state:
        data = await state.get_data()
        person = data.get("person")
        if not isinstance(person, str) or not person:
            await state.set_state(Flow.person)
            await message.answer("Сначала пришлите своё фото.", reply_markup=back_kb())
            return

        user = await ensure_user(message.from_user.id)
        own = user_extra(user)
        pending = {
            "kind": "tryon",
            "product": uri,
            "person": person,
            "extra": own,
        }

        async def job() -> bytes:
            return await fashn.generate_tryon(uri, person, own)

        await run_generation(message, state, job, "tryon", pending)
        return

    await message.answer("Сначала выберите действие кнопкой.", reply_markup=main_kb())


@router.message(Flow.garment, ~F.photo)
@router.message(Flow.person, ~F.photo)
@router.message(Flow.tryon_garment, ~F.photo)
@router.message(Flow.hanger, ~F.photo)
@router.message(Flow.pose, ~F.photo)
@router.message(Flow.photoset, ~F.photo)
async def not_photo(message: Message) -> None:
    if message.text and message.text.startswith("/"):
        return
    await message.answer("Пришлите изображение, не файл и не текст.")


async def show_after_extra(message: Message, state: FSMContext, user_id: int, *, edit: bool) -> None:
    data = await state.get_data()
    back = data.get("extra_back")
    if back == "clothes":
        await state.set_state(Flow.garment)
        await show_clothes(message, user_id, edit=edit)
        return
    if back == "product":
        await state.clear()
        await show_product(message, user_id, edit=edit)
        return
    if back == "place":
        placement = data.get("placement")
        if placement not in PLACEMENTS:
            await state.clear()
            await show_product(message, user_id, edit=edit)
            return
        await state.set_state(Flow.hanger)
        await state.update_data(placement=placement)
        user = await ensure_user(user_id)
        await show(
            message,
            confirm_text(
                "Фото вещи.",
                f"{PLACEMENTS[str(placement)].lower()}, без человека.\n{extra_line(user)}",
                int(user["balance"]),
                GENERATION_COST,
            ),
            place_kb(),
            edit=edit,
        )
        return
    await state.clear()
    await show_settings(message, user_id, edit=edit)


@router.callback_query(F.data.in_({"extra", "extra:product", "extra:place"}))
async def on_extra(query: CallbackQuery, state: FSMContext) -> None:
    if query.message is None:
        await query.answer()
        return
    raw = query.data or "extra"
    current = await state.get_state()
    if raw == "extra:product":
        back = "product"
    elif raw == "extra:place":
        back = "place"
    elif current == Flow.garment.state:
        back = "clothes"
    else:
        back = "settings"
    await state.set_state(Flow.extra)
    await state.update_data(extra_back=back)
    user = await ensure_user(query.from_user.id)
    await show(
        query.message,
        "Напишите детали, которые добавить к запросу.\n\n"
        f"{extra_line(user)}\n\n"
        "Текст добавится к генерации по фото, фотосету, примерке и фото вещи.",
        extra_kb(bool(user_extra(user))),
        edit=True,
    )
    await query.answer()


@router.callback_query(F.data == "extra_clear")
async def on_extra_clear(query: CallbackQuery, state: FSMContext) -> None:
    if query.message is None:
        await query.answer()
        return
    await update_prefs(query.from_user.id, extra="")
    await show_after_extra(query.message, state, query.from_user.id, edit=True)
    await query.answer("Свой промпт убран")


@router.message(Flow.extra, F.text)
async def on_extra_text(message: Message, state: FSMContext) -> None:
    if not message.from_user or not message.text or message.text.startswith("/"):
        return
    text = " ".join(message.text.split())[:300]
    if not text:
        await message.answer("Напишите детали текстом.")
        return
    await update_prefs(message.from_user.id, extra=text)
    await message.answer(f"Свой промпт: {text}")
    await show_after_extra(message, state, message.from_user.id, edit=False)


@router.callback_query(F.data.startswith("regen"))
async def on_regen(query: CallbackQuery, state: FSMContext) -> None:
    raw = query.data or ""
    job_id = raw.split(":", 1)[1] if raw.startswith("regen:") else ""
    last = _jobs.get(job_id) if job_id else None
    if last is None:
        last = _last_job.get(query.from_user.id)
    if not last:
        await query.answer("Сначала получите фото, потом его можно перегенерировать.", show_alert=True)
        return
    _last_job[query.from_user.id] = last
    await state.set_state(Flow.regen)
    await state.update_data(regen_job=job_id)
    if query.message:
        if last.get("kind") == "photoset":
            price = set_price(bool(last.get("face_lock")))
            text = (
                f"Перегенерация сета стоит {token_count(price)}.\n\n"
                f"Сейчас: {SETS.get(str(last.get('set_id')), {}).get('title', 'Сет')}"
                f"{', сохранение лица' if last.get('face_lock') else ''}.\n\n"
                f"{DISCLAIMER}\n\n"
                "Напишите, что изменить. Комментарий добавится ко всем 5 кадрам. "
                "Одежда останется той же."
            )
        else:
            text = (
                f"Перегенерация стоит {token_count(GENERATION_COST)}.\n\n"
                f"{DISCLAIMER}\n\n"
                "Напишите, что изменить. Комментарий добавится к запросу. "
                "Исходное фото одежды и выбранная поза останутся теми же."
            )
        await query.message.answer(text)
    await query.answer()


@router.message(Flow.regen, F.text)
async def on_regen_text(message: Message, state: FSMContext) -> None:
    if not message.from_user or not message.text or message.text.startswith("/"):
        return
    note = " ".join(message.text.split())[:400]
    if not note:
        await message.answer("Напишите, что исправить.")
        return
    data = await state.get_data()
    job_id = str(data.get("regen_job") or "")
    last = _jobs.get(job_id) if job_id else None
    if last is None:
        last = _last_job.get(message.from_user.id)
    if not last:
        await state.clear()
        await message.answer("Исходное фото уже не сохранено. Пришлите его ещё раз.", reply_markup=main_kb())
        return
    kind = last.get("kind")
    if kind == "photoset":
        await run_photoset(
            message,
            state,
            str(last["product"]),
            str(last.get("set_id") or ""),
            note,
            face_lock=bool(last.get("face_lock")),
        )
        return

    async def job() -> bytes:
        if kind == "clothes":
            prompt = f"{last['prompt']} User correction: {note}. Keep every other garment detail unchanged."
            return await fashn.generate_from_garment(
                str(last["product"]),
                prompt,
                str(last["aspect"]),
                str(last.get("pose") or ""),
            )
        if kind == "tryon":
            saved = str(last.get("extra") or "")
            combined = " ".join(part for part in (saved, note) if part)
            return await fashn.generate_tryon(str(last["product"]), str(last["person"]), combined)
        saved = str(last.get("extra") or "")
        combined = " ".join(part for part in (saved, note) if part)
        return await fashn.generate_on_hanger(
            str(last["product"]),
            str(last.get("placement") or "hanger"),
            combined,
            packshot_context(str(last.get("placement") or "")),
        )

    mode = "clothes" if kind == "clothes" else "tryon" if kind == "tryon" else f"place:{last.get('placement') or 'hanger'}"
    await run_generation(message, state, job, mode, last)


@router.message(F.text)
async def fallback(message: Message) -> None:
    if message.text and message.text.startswith("/"):
        return
    await message.answer("Выберите действие кнопкой.", reply_markup=main_kb())


async def main() -> None:
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
