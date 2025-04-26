import json
import os
from pathlib import Path

import litellm
from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm

litellm._turn_on_debug()

# os.environ["OLLAMA_CONTEXT_LENGTH"] = "4096" # increase context length for Ollama but makes it slower
os.environ["OLLAMA_KEEP_ALIVE"] = "-1"
os.environ["OPENAI_API_KEY"] = "unused"
os.environ["OPENAI_API_BASE"] = "http://localhost:11434/v1"

USE_OLLAMA = True

OLLAMA_MODEL = "qwen2.5"

model = LiteLlm(model=f"openai/{OLLAMA_MODEL}") if USE_OLLAMA else "gemini-2.0-flash"


RECIPES_DB = json.loads(Path("artefacts/recipes.json").read_text())
MACROS_DB = json.loads(Path("artefacts/macros.json").read_text())

COORDINATOR_PROMPT = """
You are the Recipe Coordinator, overseeing the meal recommendation process to ensure users receive optimal suggestions.

## System Overview
You coordinate between:
1. The User Info Agent: Collects ingredients and dietary goals from the user
2. The Recipe Finder & Recommender Agent: Finds recipes matching ingredients and recommends based on dietary goals

## Your Responsibilities
- Ensure all required information is collected before processing
- Maintain a smooth conversation flow
- Ensure proper data flow between agents
- Verify that recommendations truly match user dietary goals

## Workflow Management
1. COLLECTION PHASE:
   - Direct user queries to the User Info Agent
   - Ensure User Info Agent collects both ingredients list and dietary goals
   - When both pieces of information are collected, move to the next phase

2. RECIPE FINDING & RECOMMENDATION PHASE:
   - When information is collected, direct it to the Recipe Finder & Recommender Agent
   - This agent will find matching recipes, calculate macros, and recommend options that align with dietary goals
   - If no recipes are found, ask user for more ingredients

Remain invisible to the user - they should experience a seamless conversation flow while you work in the background.
"""

USER_INFO_PROMPT = """
You are a User Info Agent that collects necessary information from users to help them find suitable recipes.

## Your ONLY Goal
Collect two essential pieces of information from the user:
1. Available ingredients
2. Dietary goal

## Approach
- Be conversational and friendly when asking for information
- For ingredients, ask what they have available in their kitchen
- For dietary goals, ask what nutritional needs they're trying to meet

## Examples of Dietary Goals
- High protein
- Low carb
- Balanced diet
- Weight loss
- Muscle gain
- Vegan/vegetarian options


Do NOT attempt to recommend recipes yourself. Your ONLY job is to collect the necessary information.
"""

RECIPE_RECOMMENDER_PROMPT = """
You are a Recipe Recommender Agent that helps users find and select recipes that match both their available ingredients and dietary goals.

## REQUIRED WORKFLOW
You MUST follow these exact steps in order:
1. Receive the ingredients list and dietary goal from the user
2. Use `find_recipies(ingredients_list)` to get matching recipe names
3. For EACH matching recipe, you MUST call `calculate_recipe_macros(recipe_name)` to get its nutritional data
4. ONLY AFTER obtaining the macro data for ALL recipes, analyze which best match the user's dietary goal
5. Return personalized recommendations with explanations based on the calculated macros

## Tool Usage Requirements
- You MUST use `calculate_recipe_macros(recipe_name)` for EVERY recipe before making recommendations
- You are FORBIDDEN from using your own knowledge to estimate recipe macros
- If `calculate_recipe_macros()` returns None for a recipe, exclude it from recommendations

## Available Tools
- `find_recipies(ingredients_list)`: Returns recipe names matching the ingredients (allows up to 2 missing ingredients)
- `calculate_recipe_macros(recipe)`: Returns macros for a recipe as {"protein": P, "carbs": C, "fat": F, "calories": C}


## Dietary Analysis Guidelines
- High protein: Select recipes with highest protein content
- Low carb: Select recipes with lowest carbohydrate content 
- Weight loss: Focus on lower calorie, nutrient-dense options
- Muscle gain: Prioritize protein-rich recipes with adequate calories
- Balanced diet: Choose recipes with even distribution of macronutrients
- Vegan/vegetarian: Ensure no animal products (if specified)

## Response Format
For each recommendation, you MUST include:
- Recipe name
- Complete macro breakdown (protein, carbs, fat, calories) from `calculate_recipe_macros()`
- Why this recipe suits their dietary goal based on the calculated macros
- Any minor modifications that could enhance the recipe for their specific goal (optional)

IMPORTANT: DO NOT recommend any recipe without first calling `calculate_recipe_macros()` for it.
"""


def find_recipies(available_ingredients: list[str], max_missing: int = 2) -> list[str]:
    """
    Finds recipes that match a list of available ingredients, allowing for some missing ingredients.

    Args:
        available_ingredients: A list of ingredients the user has.
        max_missing: The maximum number of ingredients allowed to be missing from
                     the available list for a recipe to still be considered a match.

    Returns:
        A list of recipe names that match the criteria.
    """

    available_set = set(ingredient.lower() for ingredient in available_ingredients)
    matching_recipes = []

    for recipe in RECIPES_DB:
        required_set = set(
            ingredient["name"].lower() for ingredient in recipe["ingredients"]
        )

        # Find ingredients required by the recipe but not available
        missing_ingredients = required_set - available_set

        if len(missing_ingredients) <= max_missing:
            matching_recipes.append(recipe["name"])

    return matching_recipes


def calculate_recipe_macros(recipe: str) -> dict | None:
    """
    Calculates the total estimated macros for a given recipe name, using ingredient
    weights from the recipe and macro data per 100g from the ingredient DB.

    Args:
        recipe: The name of the recipe to calculate macros for.
    Returns:
        A dictionary with total {"protein": P, "carbs": C, "fat": F, "calories": C} (rounded),
        or None if the recipe name is not found or essential data is missing.
    """
    # Find the recipe in the database
    target_recipe_data = next(
        (r for r in RECIPES_DB if r.get("name", "").lower() == recipe.lower()), None
    )

    if target_recipe_data is None:
        print(f"Warning: Recipe '{recipe}' not found in the database.")
        return None

    total_macros = {
        "protein": 0.0,
        "carbs": 0.0,
        "fat": 0.0,
        "calories": 0.0,
    }

    # Process each ingredient in the recipe
    for ingredient_obj in target_recipe_data["ingredients"]:
        ingredient_name = ingredient_obj.get("name")
        weight_grams = ingredient_obj.get("weight_grams")

        # Skip ingredients with missing data
        if not ingredient_name or not weight_grams:
            print(f"Warning: Missing data for ingredient in recipe '{recipe}'")
            continue

        ingredient_name_lower = ingredient_name.lower()
        macros_per_100g = MACROS_DB.get(ingredient_name_lower)

        # Skip ingredients not found in the macros database
        if not macros_per_100g:
            print(
                f"Warning: No macro data found for '{ingredient_name}' in recipe '{recipe}'"
            )
            continue

        # Calculate the scaling factor (weight / 100) and add to totals
        scale_factor = weight_grams / 100.0
        for macro in total_macros:
            total_macros[macro] += macros_per_100g.get(macro, 0) * scale_factor

    # Round final totals for cleaner output
    return {macro: round(value, 1) for macro, value in total_macros.items()}


user_info_agent = Agent(
    name="user_info_agent",
    model=model,
    instruction=USER_INFO_PROMPT,
    description="Collects ingredients and dietary goals from users.",
)

recipe_finder_recommender_agent = Agent(
    name="recipe_recommender_agent",
    model=model,
    instruction=RECIPE_RECOMMENDER_PROMPT,
    description="Finds recipes based on available ingredients, calculates their macros and makes personalized recommendations based on dietary goals.",
    tools=[find_recipies, calculate_recipe_macros],
)

root_agent = Agent(
    name="coordinator_agent",
    model=model,
    instruction=COORDINATOR_PROMPT,
    global_instruction="Your goal is to help users find recipes based on their available ingredients and dietary goals.",
    description="Agent that coordinates the workflow between information collection, recipe finding, and recommendation.",
    sub_agents=[user_info_agent, recipe_finder_recommender_agent],
)
