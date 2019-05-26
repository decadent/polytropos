from typing import Dict

from etl4.ontology.metamorphosis.__change import Change
from etl4.ontology.metamorphosis.__lookup import lookup
from etl4.ontology.metamorphosis.__subject import subject
from etl4.ontology.schema import Schema
from etl4.ontology.variable import Variable
from etl4.util import nesteddicts

class CalculateWeightGain(Change):
    """Determine the total weight gain over the observation period."""

    @subject("weight_var", data_types={"Decimal"}, temporal=1)
    @subject("weight_gain_var", data_types={"Decimal"}, temporal=-1)
    def __init__(self, schema: Schema, lookups: Dict, weight_var, weight_gain_var):
        super().__init__(schema, lookups, weight_var, weight_gain_var)
        self.weight_var: Variable = weight_var
        self.weight_gain_var: Variable = weight_gain_var

    def __call__(self, composite: Dict):
        periods = set(composite.keys()) - {"invariant"}
        earliest = min(periods)
        latest = max(periods)

        weight_path = list(self.weight_var.absolute_path)

        earliest_weight_path: list = [earliest] + weight_path
        earliest_weight: float = nesteddicts.get(composite, earliest_weight_path)

        latest_weight_path: list = [latest] + weight_path
        latest_weight: float = nesteddicts.get(composite, latest_weight_path)

        # I know, should have called it "weight change."
        weight_gain = latest_weight - earliest_weight

        weight_gain_path = ["invariant"] + list(self.weight_gain_var.absolute_path)
        nesteddicts.put(composite, weight_gain_path, weight_gain)

class DetermineGender(Change):
    """Use a lookup table to determine the person's gender."""
    @lookup("genders")
    @subject("person_name_var", data_types={"Text"}, temporal=-1)
    @subject("gender_var", data_types={"Text"}, temporal=-1)
    def __init__(self, schema: Schema, lookups: Dict, person_name_var, gender_var):
        super().__init__(schema, lookups, person_name_var, gender_var)
        self.person_name_var: Variable = person_name_var
        self.gender_var: Variable = gender_var

    def __call__(self, composite: Dict):
        person_name_path = ["invariant"] + list(self.person_name_var.absolute_path)
        person_name: str = nesteddicts.get(composite, person_name_path)
        lc_name = person_name.lower()

        gender = self.lookups["genders"][lc_name]
        gender_path = ["invariant"] + list(self.gender_var.absolute_path)
        nesteddicts.put(composite, gender_path, gender)