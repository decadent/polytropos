import logging
import json
from abc import abstractmethod
from collections import defaultdict
from typing import List as ListType, Dict, Iterator, TYPE_CHECKING, Optional, Set, Any, NewType
from functools import partial
from cachetools import cachedmethod
from cachetools.keys import hashkey
from polytropos.util.nesteddicts import path_to_str
from datetime import datetime

if TYPE_CHECKING:
    from polytropos.ontology.track import Track

VariableId = NewType("VariableId", str)


class Validator:
    @staticmethod
    def validate_sources(variable: "Variable", sources: ListType[VariableId], init: bool = False) -> None:
        if not init:
            _check_folder_has_sources(variable, sources)
        if sources:
            for source in sources:
                #_verify_source_parent(variable, source)
                _verify_source_exists(variable, source)
                _verify_source_compatible(variable, source)

    @staticmethod
    def validate_parent(variable: "Variable", parent: Optional[VariableId]) -> None:
        if parent is None:
            return
        if parent == "":
            raise ValueError("Parent id is an empty string")
        if parent not in variable.track:
            # invalid parent
            raise ValueError('Nonexistent parent')
        if not isinstance(variable.track[parent], Container):
            # parent not container
            raise ValueError('Parent is not a container')
        if (
                isinstance(variable, GenericList) and
                variable.descends_from_list
        ):
            logging.debug('Nested list: %s', variable)

    @staticmethod
    def validate_name(variable: "Variable", name: str) -> None:
        if '/' in name or '.' in name:
            raise ValueError
        sibling_names = set(
            variable.track[sibling].name
            for sibling in variable.siblings
            if sibling != variable.var_id
        )
        if name in sibling_names:
            raise ValueError('Duplicate name with siblings')

    @staticmethod
    def validate_sort_order(variable: "Variable", sort_order: int, adding: bool = False) -> None:
        if sort_order < 0:
            raise ValueError
        # This line is very slow. Consider adding a cache for variable.siblings.
        if sort_order >= len(list(variable.siblings)) + (1 if adding else 0):
            raise ValueError('Invalid sort order')

    @staticmethod
    def validate_var_id(var_id: VariableId) -> None:
        if var_id == "":
            raise ValueError("Variable id is an empty string")

    @classmethod
    def validate(cls, variable: "Variable", init: bool = False, adding: bool = False) -> None:
        """Run validation on the variable, init=True disables some of the
        validation that shouldn't run during schema initialization. For
        example, we might create a child before a parent.
        The parameter adding is only used for validating the sort order. We
        need it because when we are adding a new variable the sort order logic
        is slightly different (because we will end up having one more
        sibling"""
        cls.validate_var_id(variable.var_id)
        cls.validate_parent(variable, variable.parent)
        cls.validate_name(variable, variable.name)

        if variable.track.source is not None:
            cls.validate_sources(variable, variable.sources, init)

        # TODO This line is extremely slow. I suspect that putting a cache on 'Variable.children' would solve it
        cls.validate_sort_order(variable, variable.sort_order, adding)


class Variable:
    def __init__(self, track: "Track", var_id: VariableId, name: str, sort_order: int,
                 notes: Optional[str] = None, earliest_epoch: Optional[str] = None, latest_epoch: Optional[str] = None,
                 short_description: Optional[str] = None, long_description: Optional[str] = None,
                 sources: Optional[ListType[VariableId]] = None, parent: Optional[VariableId] = None):
        self.initialized = False

        # The track to which this variable belongs
        self.track: "Track" = track

        # The variable id of the variable in the corresponding track.
        # WARNING! The variable ID _MUST_ be unique within the schema, or terrible things will happen!
        self.var_id: VariableId = var_id

        # The name of the node, as used in paths. Not to be confused with its ID, which is path-immutable.
        self.name: str = name

        # The order that this variable appears in instance hierarchies.
        self.sort_order: int = sort_order

        # Metadata: any information about the variable that the operator chooses to include.
        self.notes: Optional[str] = notes

        # An alphabetically sortable indicator of when this field first came into use.
        self.earliest_epoch: Optional[str] = earliest_epoch

        # An alphabetically sortable indicator of when this field ceased to be used.
        self.latest_epoch: Optional[str] = latest_epoch

        # Descriptions of the variable -- used in various situations
        self.short_description: Optional[str] = short_description
        self.long_description: Optional[str] = long_description

        # The variable IDs (not names!) from the preceding stage from which to derive values for this variable, if any.
        self.sources: ListType[VariableId] = sources if sources is not None else []

        # The container variable above this variable in the hierarchy, if any.
        self.parent: Optional[VariableId] = parent

        self._cache: Dict = {}

        self.initialized = True

    def __hash__(self) -> int:
        return hash(self.var_id) if self.var_id is not None else 0

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, self.__class__) and other.var_id == self.var_id

    def __setattr__(self, attribute: str, value: Any) -> None:
        if attribute != "initialized" and self.initialized:
            value = self.validate_attribute_value(attribute, value)

        self.__dict__[attribute] = value

    def validate_attribute_value(self, attribute: str, value: Any) -> Any:
        if attribute == 'var_id':
            Validator.validate_var_id(value)
        elif attribute == 'name':
            Validator.validate_name(self, value)
        elif attribute == 'sources':
            Validator.validate_sources(self, value)
            if self.track and isinstance(self, GenericList):
                child_sources: Dict[VariableId, ListType[Variable]] = defaultdict(list)
                for child in self.children:
                    for source_id in child.sources:
                        child_sources[source_id].append(child)
                safe = set()
                assert self.track.source is not None
                for source_id in value:
                    source_var = self.track.source[source_id]
                    for child_source in child_sources:
                        if source_var.check_ancestor(child_source):
                            safe.add(child_source)
                for child_source, children in child_sources.items():
                    if child_source not in safe:
                        for child in children:
                            child.sources.remove(child_source)
        elif attribute == 'parent':
            Validator.validate_parent(self, value)
        elif attribute == 'sort_order':
            Validator.validate_sort_order(self, value)
        elif attribute in {'notes', 'earliest_epoch', 'latest_epoch', 'short_description', 'long_description'}:
            if value is not None:
                return value.strip()
        elif attribute == 'data_type':
            raise AttributeError

        if attribute in {'sort_order', 'parent', 'name'}:
            self.track.invalidate_variables_cache()

        return value

    def invalidate_cache(self) -> None:
        logging.debug("Invaliding cache for variable %s." % self.var_id)
        self._cache.clear()

    def update_sort_order(self, old_order: Optional[int] = None, new_order: Optional[int] = None) -> None:
        if old_order is None:
            old_order = len(list(self.siblings)) + 1
        if new_order is None:
            new_order = len(list(self.siblings)) + 1
        for sibling in self.siblings:
            if sibling == self.var_id:
                continue
            diff = 0
            if self.track[sibling].sort_order >= new_order:
                diff += 1
            if self.track[sibling].sort_order >= old_order:
                diff -= 1
            self.track[sibling].__dict__['sort_order'] += diff

    @property
    def temporal(self) -> bool:
        return self.track.schema is not None and self.track.schema.is_temporal(self.var_id)

    @property
    def siblings(self) -> Iterator[VariableId]:
        if self.parent is None:
            return map(lambda root: root.var_id, self.track.roots)
        return map(
            lambda child: child.var_id,
            self.track[self.parent].children
        )

    @property
    def has_targets(self) -> bool:
        """True iff any downstream track contains a variable that depends on this one."""
        return any(self.targets())

    @property
    def descends_from_list(self) -> bool:
        """True iff this or any upstream variable is a list or named list."""
        if not self.parent:
            return False
        parent = self.track[self.parent]
        return isinstance(parent, GenericList) or parent.descends_from_list

    @property  # type: ignore # Decorated property not supported
    @cachedmethod(lambda self: self._cache, key=partial(hashkey, 'relative_path'))
    def relative_path(self) -> ListType[str]:
        """The path from this node to the nearest list or or root."""
        if not self.parent:
            return [self.name]
        parent: "Variable" = self.track[self.parent]
        if isinstance(parent, GenericList):
            return [self.name]
        parent_path: ListType = parent.relative_path
        return parent_path + [self.name]

    @property  # type: ignore # Decorated property not supported
    @cachedmethod(lambda self: self._cache, key=partial(hashkey, 'absolute_path'))
    def absolute_path(self) -> ListType[str]:
        """The path from this node to the root."""
        if not self.parent:
            return [self.name]
        parent_path: ListType = self.track[self.parent].absolute_path
        return parent_path + [self.name]

    @property  # type: ignore # Decorated property not supported
    @cachedmethod(lambda self: self._cache, key=partial(hashkey, 'tree'))
    def tree(self) -> Dict:
        """A tree representing the descendants of this node. (For UI)"""
        children = [
            child.tree
            for child in sorted(
                self.children, key=lambda child: child.sort_order
            )
        ]
        tree: Dict[str, Any] = dict(
            title=self.name,
            varId=self.var_id,
            dataType=self.data_type,
        )
        if children:
            tree['children'] = children
        return tree

    def dump(self) -> Dict:
        """A dictionary representation of this variable."""
        representation = {
            'name': self.name,
            'data_type': self.data_type,
            'sort_order': self.sort_order
        }
        for field_name, field_value in vars(self).items():
            if field_name == 'name' or field_name == 'sort_order' or field_name == 'var_id' or field_name == 'track' or field_name == 'initialized':
                continue
            if field_value:
                representation[field_name] = field_value
        return representation

    def dumps(self) -> str:
        """A JSON-compatible representation of this variable. (For serialization.)"""
        return json.dumps(self.dump(), indent=4)

    def check_ancestor(self, child_id: VariableId, stop_at_list: bool = False) -> bool:
        variable = self.track[child_id]
        if variable.parent is None:
            return False
        if (
                stop_at_list and
                isinstance(self.track[variable.parent], GenericList)
        ):
            return False
        if variable.parent == self.var_id:
            return True
        return self.check_ancestor(variable.parent)

    def get_first_list_ancestor(self) -> Optional["Variable"]:
        parent_id = self.parent
        if parent_id is None:
            return None
        parent = self.track[parent_id]
        if isinstance(parent, GenericList):
            return parent
        return parent.get_first_list_ancestor()

    @cachedmethod(lambda self: self._cache, key=partial(hashkey, 'descendants_that'))
    def descendants_that(self, data_type: str=None, targets: int=0, container: int=0, inside_list: int=0) \
            -> Iterator[str]:
        """Provides a list of variable IDs descending from this variable that meet certain criteria.
        :param data_type: The type of descendant to be found.
        :param targets: If -1, include only variables that lack targets; if 1, only variables without targets.
        :param container: If -1, include only primitives; if 1, only containers.
        :param inside_list: If -1, include only elements outside lists; if 1, only inside lists.
        """
        for variable_id in self.track.descendants_that(
            data_type, targets, container, inside_list
        ):
            if self.check_ancestor(variable_id, stop_at_list=True):
                yield variable_id

    def targets(self) -> Iterator[VariableId]:
        """Returns an iterator of the variable IDs for any variables that DIRECTLY depend on this one in the specified
        stage. Raises an exception if this variable's stage is not the source stage for the specified stage."""
        if self.track.target:
            for variable_id, variable in self.track.target.items():
                if self.var_id in variable.sources:
                    yield variable_id

    @property
    def children(self) -> Iterator["Variable"]:
        # TODO Consider caching the list of children for each variable in Track.
        return filter(
            lambda variable: variable.parent == self.var_id,
            self.track.values()
        )

    @property
    def data_type(self) -> str:
        return self.__class__.__name__

    def ancestors(self, parent_id_to_stop: Optional[VariableId]) -> Iterator["Variable"]:
        """Returns an iterator of ancestors (self, self.parent, self.parent.parent, etc).
        The first item - the current variable.
        If the parent_id_to_stop parameter is None all ancestors are returned.
        Otherwise the last item is the ancestor with parent identifier equal to parent_id_to_stop."""
        current = self
        yield current
        while current.parent is not None and current.parent != parent_id_to_stop:
            current = self.track[current.parent]
            yield current


class Container(Variable):
    pass


class Primitive(Variable):
    @abstractmethod
    def cast(self, value: Optional[Any]) -> Optional[Any]:
        pass


class Integer(Primitive):
    def cast(self, value: Optional[Any]) -> Optional[int]:
        if value is None or value == "":
            return None
        return int(value)


class Text(Primitive):
    def cast(self, value: Optional[Any]) -> Optional[str]:
        if value is None or value == "":
            return None
        return str(value)


class Decimal(Primitive):
    def cast(self, value: Optional[Any]) -> Optional[float]:
        if value is None or value == "":
            return None
        return float(value)


class Unary(Primitive):
    def cast(self, value: Optional[Any]) -> Optional[bool]:
        if value is None or value == "":
            return None
        if value is True:
            return True
        if not (isinstance(value, str) and value.lower() == "x"):
            raise ValueError
        return True


class Binary(Primitive):
    def cast(self, value: Optional[Any]) -> Optional[bool]:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            return value
        vl = value.lower()
        if vl in {"1", "true"}:
            return True
        if vl in {"0", "false"}:
            return False
        raise ValueError


class Currency(Primitive):
    def cast(self, value: Optional[Any]) -> Optional[float]:
        if value is None or value == "":
            return None
        return float(value)


class Phone(Primitive):
    def cast(self, value: Optional[Any]) -> Optional[str]:
        if value is None or value == "":
            return None
        return str(value)


class Email(Primitive):
    def cast(self, value: Optional[Any]) -> Optional[str]:
        if value is None or value == "":
            return None
        return str(value)


class URL(Primitive):
    def cast(self, value: Optional[Any]) -> Optional[str]:
        if value is None or value == "":
            return None
        return str(value)


class Date(Primitive):
    def cast(self, value: Optional[Any]) -> Optional[str]:
        if value is None or value in {"", "000000"}:
            return None
        if len(value) == 6 and value.isdecimal():
            year: str = value[:4]
            month: str = value[4:]
            return "%s-%s-01" % (year, month)

        if len(value) >= 10:
            retained = value[:10]

            # Will raise a ValueError if unexpected content
            datetime.strptime(retained, "%Y-%m-%d")

            return retained

        raise ValueError


class Folder(Container):
    @property
    def has_targets(self) -> bool:
        return False

    def targets(self) -> Iterator[VariableId]:
        raise AttributeError


class GenericList(Container):
    pass


class List(GenericList):
    pass


class NamedList(GenericList):
    pass

def _incompatible_type(source_var: Variable, variable: Variable) -> bool:
    if variable.__class__ == List:
        if source_var.__class__ not in {List, Folder}:
            return True
    elif source_var.__class__ != variable.__class__:
        return True
    return False

def _check_folder_has_sources(variable: "Variable", sources: ListType[VariableId]) -> None:
    if len(sources) > 0 and isinstance(variable, Folder):
        var_id: VariableId = variable.var_id
        source_str = ", ".join(sources)
        msg_template: str = 'Folders can\'t have sources, but variable "%s" is a Folder and lists the following ' \
                            'sources: %s'
        raise ValueError(msg_template % (var_id, source_str))

def _verify_source_parent(variable: "Variable", source_var_id: VariableId) -> None:
    list_ancestor: Optional["Variable"] = variable.get_first_list_ancestor()
    if list_ancestor is None:
        return
    parent_sources: Set[VariableId] = set(list_ancestor.sources)
    assert variable.track.source is not None
    source: "Variable" = variable.track.source[source_var_id]
    while source.parent is not None and source.var_id not in parent_sources:
        source = variable.track.source[source.parent]
    if source.var_id not in parent_sources:
        template: str = 'Variable %s (%s), which descends from %s %s (%s), includes %s (%s) as a source, but that ' \
                        'does not descend from one of the root list\'s sources.'
        msg = template % (
            path_to_str(variable.absolute_path),
            variable.var_id,
            list_ancestor.data_type,
            path_to_str(list_ancestor.absolute_path),
            list_ancestor.var_id,
            path_to_str(source.absolute_path),
            source.var_id
        )
        raise ValueError(msg)

def _verify_source_exists(variable: "Variable", source_var_id: VariableId) -> None:
    assert variable.track.source is not None
    if source_var_id not in variable.track.source:
        var_id: VariableId = variable.var_id
        source_track_name: str = variable.track.source.name
        msg_template: str = 'Variable "%s" is attempting to add source variable "%s", which does not exist in the ' \
                            'source track "%s"'
        raise ValueError(msg_template % (var_id, source_var_id, source_track_name))

def _verify_source_compatible(variable: "Variable", source_var_id: VariableId) -> None:
    assert variable.track.source is not None
    source_var = variable.track.source[source_var_id]
    if _incompatible_type(source_var, variable):
        var_id: VariableId = variable.var_id
        var_type: str = variable.__class__.__name__
        source_var_type: str = source_var.__class__.__name__
        msg_template: str = 'Variable "%s" (%s) is attempting to add incompatible source variable %s (%s)'
        raise ValueError(msg_template % (var_id, var_type, source_var_id, source_var_type))
