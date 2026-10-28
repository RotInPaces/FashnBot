"""Telegram-бот примерки одежды через FASHN."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
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
EXAMPLE_NAMES = ("before-1", "after-1", "before-2", "after-2")
EXAMPLE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
_balance_lock = asyncio.Lock()
_busy: set[int] = set()

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
    "bed": "На кровати",
    "floor": "На полу",
}


class Flow(StatesGroup):
    garment = State()
    person = State()
    tryon_garment = State()
    hanger = State()
    regen = State()
    pose = State()


_last_job: dict[int, dict] = {}
# Своя поза: data URI только в памяти, на диск не пишем.
_pose_ref: dict[int, str] = {}


# Рубли и кредиты. Подставляются в QR, когда появится банк.
PACKAGES: tuple[tuple[int, int], ...] = ((100, 10), (300, 35), (500, 60))


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
        data[key] = user
        _save_balances(data)
        return dict(user)


async def spend(user_id: int) -> int | None:
    async with _balance_lock:
        data = _load_balances()
        key = str(user_id)
        user = data.get(key)
        if not isinstance(user, dict):
            return None
        balance = int(user.get("balance", 0))
        if balance < GENERATION_COST:
            return None
        user["balance"] = balance - GENERATION_COST
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


def prompt_for(user: dict, *, pose_photo: bool = False) -> str:
    interior = INTERIOR_PROMPTS.get(str(user.get("interior")), INTERIOR_PROMPTS["mirror"])
    if pose_photo:
        frame = (
            "Match only the body pose from the pose reference: stance, arms, legs, head angle and crop. "
            "Do not copy that reference person's face, hair, identity, clothes, colors, lighting or room"
        )
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
    )


def aspect_for(user: dict) -> str:
    return FRAME_RATIOS.get(str(user.get("frame")), FRAME_RATIOS["full"])


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
    return line


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
        "Бот собирает фото одежды для карточек маркетплейса и отзывов.\n\n"
        "Можно сгенерировать человека в вашей вещи, примерить её на своё фото "
        "или снять саму вещь на вешалке, кровати или полу.\n\n"
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
        ]
    )


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
            [InlineKeyboardButton(text="К генерации", callback_data="ready")],
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


def pose_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="back")]]
    )


def result_kb(mode: str) -> InlineKeyboardMarkup:
    if mode.startswith("place:"):
        repeat = mode
    else:
        repeat = {"tryon": "tryon"}.get(mode, "ready")
    rows = [
        [InlineKeyboardButton(text="Ещё раз", callback_data=repeat)],
        [InlineKeyboardButton(text="Перегенерировать", callback_data="regen")],
    ]
    if mode != "clothes":
        rows.append([InlineKeyboardButton(text="Генерация по фото одежды", callback_data="clothes")])
    if mode == "clothes":
        rows.append([InlineKeyboardButton(text="Настройки", callback_data="settings")])
    rows.append([InlineKeyboardButton(text="Пополнить", callback_data="topup")])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def topup_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{rubles} ₽ — {token_count(credits)}", callback_data=f"pay:{rubles}")]
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


def example_files() -> list[Path] | None:
    found: list[Path] = []
    for name in EXAMPLE_NAMES:
        match = next(
            (EXAMPLES_DIR / f"{name}{suffix}" for suffix in EXAMPLE_SUFFIXES if (EXAMPLES_DIR / f"{name}{suffix}").is_file()),
            None,
        )
        if match is None:
            return None
        found.append(match)
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
        "Настройки выбраны. Пришлите одно фото одежды.\n\n"
        f"Сейчас: {prefs_line(user, user_id)}\n"
        f"{pose_note}"
        f"Стоимость: {token_count(GENERATION_COST)}. На счёте: {token_count(user['balance'])}.\n\n"
        f"{DISCLAIMER}\n\n"
        "Лучше фото вещи на ровном фоне."
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


async def run_generation(message: Message, state: FSMContext, job, mode: str) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if user_id in _busy:
        await message.answer("Предыдущая генерация ещё идёт. Дождитесь результата.")
        return

    user = await ensure_user(user_id)
    if int(user["balance"]) < GENERATION_COST:
        await message.answer(
            f"Недостаточно токенов. Нужно {token_count(GENERATION_COST)}, на счёте {token_count(user['balance'])}.",
            reply_markup=main_kb(),
        )
        await state.clear()
        return

    _busy.add(user_id)
    try:
        status = await message.answer("Генерирую фото. Обычно это занимает до минуты.")
        try:
            image = await job()
        except fashn.FashnError as exc:
            logger.error("Generation failed: %s", exc.name)
            await status.edit_text(fashn.user_message(exc))
            await message.answer("Токены не списаны.", reply_markup=main_kb())
            await state.clear()
            return
        except Exception:
            logger.exception("Generation crashed")
            await status.edit_text("Не получилось сгенерировать фото. Попробуйте ещё раз.")
            await message.answer("Токены не списаны.", reply_markup=main_kb())
            await state.clear()
            return

        if not image:
            await status.edit_text("Сервис не вернул изображение.")
            await message.answer("Токены не списаны.", reply_markup=main_kb())
            await state.clear()
            return

        balance = await spend(user_id)
        if balance is None:
            await status.edit_text("Баланс закончился во время генерации. Результат не отправляю.")
            await state.clear()
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
            await message.answer(
                f"На счёте: {token_count(balance)}.\nОдна генерация — {token_count(GENERATION_COST)}.",
                reply_markup=result_kb(mode),
            )
        except Exception:
            logger.exception("Failed to send generated photo")
            returned = await add_credits(user_id, GENERATION_COST)
            await message.answer(
                f"Не удалось отправить фото. Токены возвращены. На счёте: {token_count(returned)}.",
                reply_markup=main_kb(),
            )
        await state.clear()
    finally:
        _busy.discard(user_id)


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    if message.from_user and message.from_user.id in _busy:
        await message.answer("Генерация ещё идёт. Меню откроется после результата.")
        return
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
        "Два примера. По порядку: ваше фото, результат, ваше фото, результат.\n\n"
        "Готовое изображение может немного отличаться от показанного примера."
    )
    await query.message.answer_media_group(
        [
            InputMediaPhoto(media=FSInputFile(files[0]), caption=caption),
            InputMediaPhoto(media=FSInputFile(files[1])),
            InputMediaPhoto(media=FSInputFile(files[2])),
            InputMediaPhoto(media=FSInputFile(files[3])),
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
    media = [InputMediaPhoto(media=FSInputFile(path)) for path in files]
    media[0].caption = REF_TEXT
    await query.message.answer_media_group(media)
    await query.answer()


@router.callback_query(F.data == "tryon")
async def on_tryon(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Flow.person)
    if query.message:
        user = await ensure_user(query.from_user.id)
        await show(
            query.message,
            "Пришлите своё фото.\n\n"
            "Поза, лицо и фон сохранятся. Следующим сообщением попрошу фото одежды.\n"
            f"Стоимость: {token_count(GENERATION_COST)}. На счёте: {token_count(user['balance'])}.",
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
    rows.append([InlineKeyboardButton(text="Назад", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "product")
async def on_product(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if query.message:
        await show(
            query.message,
            "Кадр вещи без человека. Куда её положить?",
            product_kb(),
            edit=True,
        )
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
        f"Пришлите одно фото одежды.\n\n"
        f"Соберу кадр: {PLACEMENTS[placement].lower()}, без человека.\n"
        f"Стоимость: {token_count(GENERATION_COST)}. На счёте: {token_count(user['balance'])}.",
        back_kb(),
        edit=True,
    )
    await query.answer()


@router.callback_query(F.data == "topup")
async def on_topup(query: CallbackQuery) -> None:
    if query.message:
        await show(
            query.message,
            "Пополнение через СБП.\n\n"
            "Банк ещё не подключён. После подключения на выбранную сумму "
            "бот пришлёт QR-код, а токены появятся после оплаты.",
            topup_kb(),
            edit=True,
        )
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
        f"Счёт на {rubles} ₽, это {token_count(credits)}.\n\n"
        "Оплата по СБП ещё не подключена. Здесь появится QR-код."
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

        _last_job[message.from_user.id] = {
            "kind": "clothes",
            "product": uri,
            "prompt": prompt,
            "aspect": ratio,
            "pose": pose,
        }

        async def job() -> bytes:
            return await fashn.generate_from_garment(uri, prompt, ratio, pose)

        await run_generation(message, state, job, "clothes")
        return

    if current == Flow.hanger.state:
        data = await state.get_data()
        placement = data.get("placement")
        if placement not in PLACEMENTS:
            placement = "hanger"

        _last_job[message.from_user.id] = {
            "kind": "place",
            "product": uri,
            "placement": str(placement),
        }

        async def hanger_job() -> bytes:
            return await fashn.generate_on_hanger(uri, str(placement))

        await run_generation(message, state, hanger_job, f"place:{placement}")
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

        _last_job[message.from_user.id] = {
            "kind": "tryon",
            "product": uri,
            "person": person,
        }

        async def job() -> bytes:
            return await fashn.generate_tryon(uri, person)

        await run_generation(message, state, job, "tryon")
        return

    await message.answer("Сначала выберите действие кнопкой.", reply_markup=main_kb())


@router.message(Flow.garment, ~F.photo)
@router.message(Flow.person, ~F.photo)
@router.message(Flow.tryon_garment, ~F.photo)
@router.message(Flow.hanger, ~F.photo)
@router.message(Flow.pose, ~F.photo)
async def not_photo(message: Message) -> None:
    if message.text and message.text.startswith("/"):
        return
    await message.answer("Пришлите изображение, не файл и не текст.")


@router.callback_query(F.data == "regen")
async def on_regen(query: CallbackQuery, state: FSMContext) -> None:
    if query.from_user.id not in _last_job:
        await query.answer("Сначала получите фото, потом его можно перегенерировать.", show_alert=True)
        return
    await state.set_state(Flow.regen)
    if query.message:
        await query.message.answer(
            f"Перегенерация стоит {token_count(GENERATION_COST)}.\n\n"
            "Напишите, что изменить. Комментарий добавится к запросу. "
            "Исходное фото одежды и выбранная поза останутся теми же."
        )
    await query.answer()


@router.message(Flow.regen, F.text)
async def on_regen_text(message: Message, state: FSMContext) -> None:
    if not message.from_user or not message.text or message.text.startswith("/"):
        return
    note = " ".join(message.text.split())[:400]
    if not note:
        await message.answer("Напишите, что исправить.")
        return
    last = _last_job.get(message.from_user.id)
    if not last:
        await state.clear()
        await message.answer("Исходное фото уже не сохранено. Пришлите его ещё раз.", reply_markup=main_kb())
        return
    kind = last.get("kind")

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
            return await fashn.generate_tryon(str(last["product"]), str(last["person"]), note)
        return await fashn.generate_on_hanger(str(last["product"]), str(last.get("placement") or "hanger"), note)

    await run_generation(message, state, job, "clothes" if kind == "clothes" else "tryon" if kind == "tryon" else f"place:{last.get('placement') or 'hanger'}")


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
