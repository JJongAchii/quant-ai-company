"""Release-reviewed recipes; neither owner messages nor models can change their scope."""

import hashlib
import json
from importlib.resources import files

from .contracts import Recipe


def load_recipe(recipe_id="kr-etf-p11-replay-v1") -> Recipe:
    if recipe_id != "kr-etf-p11-replay-v1":
        raise ValueError("unregistered_research_recipe")
    return Recipe.model_validate_json(files("quant_company.research").joinpath("reference/p11.json").read_text())


def recipe_digest(recipe: Recipe) -> str:
    value = json.dumps(recipe.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(value.encode()).hexdigest()
