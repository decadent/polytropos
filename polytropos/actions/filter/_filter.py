import logging
import os
import json
from abc import abstractmethod
from dataclasses import dataclass
from typing import Dict, Optional
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from polytropos.ontology.composite import Composite

from polytropos.ontology.schema import Schema
from polytropos.util.exceptions import ExceptionWrapper

from polytropos.util.loader import load
from polytropos.actions.step import Step


@dataclass
class Filter(Step):  # type: ignore # https://github.com/python/mypy/issues/5374
    """Iterates over each composite, removing some of them if they do not meet some criterion."""
    schema: Schema

    # noinspection PyMethodOverriding
    @classmethod
    def build(cls, path_locator, schema: Schema, name: str, mappings: Dict):  # type: ignore # Signature of "build" incompatible with supertype "Step"
        logging.info('Building instance of filter class "%s"' % name)
        filters = load(cls)
        return filters[name](schema=schema, **mappings)

    @abstractmethod
    def passes(self, composite: Composite) -> bool:
        pass

    def process_composite(self, origin_dir: str, target_dir: str, filename: str) -> Optional[ExceptionWrapper]:
        try:
            with open(os.path.join(origin_dir, filename), 'r') as origin_file:
                content: Dict = json.load(origin_file)
                composite: Composite = Composite(self.schema, content)
                if self.passes(composite):
                    with open(os.path.join(target_dir, filename), 'w') as target_file:
                        json.dump(composite.content, target_file)
        except Exception as e:
            return ExceptionWrapper(e)
        return None

    def __call__(self, origin_dir: str, target_dir: str) -> None:
        with ThreadPoolExecutor() as executor:
            results = executor.map(
                partial(self.process_composite, origin_dir, target_dir),
                os.listdir(origin_dir)
            )
            # TODO: Exceptions are supposed to propagate from a ProcessPoolExecutor. Why aren't mine?
            for result in results:  # type: Optional[ExceptionWrapper]
                if result is not None:
                    result.re_raise()
