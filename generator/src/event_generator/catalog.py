"""Reference data with rough, plausible weights for Moroccan e-commerce (synthetic)."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

CITIES: tuple[tuple[str, float], ...] = (
    ("Casablanca", 0.30),
    ("Rabat", 0.12),
    ("Marrakech", 0.11),
    ("Fès", 0.09),
    ("Tanger", 0.09),
    ("Agadir", 0.07),
    ("Kénitra", 0.06),
    ("Tétouan", 0.06),
    ("Meknès", 0.05),
    ("Oujda", 0.05),
)

# Cash on delivery is still the most common way to pay online in Morocco.
PAYMENT_METHODS: tuple[tuple[str, float], ...] = (
    ("cash_on_delivery", 0.48),
    ("card", 0.34),
    ("wallet", 0.10),
    ("bank_transfer", 0.08),
)


@dataclass(frozen=True, slots=True)
class CategoryProfile:
    name: str
    weight: float
    median_mad: float
    sigma: float  # log-normal spread: larger means more very cheap and very expensive orders


CATEGORIES: tuple[CategoryProfile, ...] = (
    CategoryProfile("fashion", 0.22, 350, 0.50),
    CategoryProfile("grocery", 0.16, 180, 0.40),
    CategoryProfile("electronics", 0.14, 1_800, 0.70),
    CategoryProfile("beauty", 0.14, 220, 0.45),
    CategoryProfile("home", 0.12, 600, 0.60),
    CategoryProfile("sports", 0.08, 450, 0.55),
    CategoryProfile("books", 0.07, 120, 0.35),
    CategoryProfile("toys", 0.07, 260, 0.45),
)

MIN_AMOUNT_MAD = 10.0
MAX_AMOUNT_MAD = 60_000.0


def pick(rng: random.Random, options: tuple[tuple[str, float], ...]) -> str:
    names = [name for name, _ in options]
    weights = [weight for _, weight in options]
    return rng.choices(names, weights)[0]


def pick_category(rng: random.Random) -> CategoryProfile:
    return rng.choices(CATEGORIES, [c.weight for c in CATEGORIES])[0]


def order_amount(rng: random.Random, category: CategoryProfile) -> str:
    """Log-normal basket value, clamped, formatted with two decimals (exact in JSON)."""
    raw = rng.lognormvariate(math.log(category.median_mad), category.sigma)
    return f"{min(max(raw, MIN_AMOUNT_MAD), MAX_AMOUNT_MAD):.2f}"
