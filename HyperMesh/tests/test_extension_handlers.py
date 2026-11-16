from __future__ import annotations

import math
import sys
import tempfile
import types
import unittest
from pathlib import Path


EXTENSION_ROOT = Path(__file__).resolve().parents[1] / "hyperworks_extension"
sys.path.insert(0, str(EXTENSION_ROOT))

from hyperworks_mcp_extension import handlers as handler_module  # noqa: E402
from hyperworks_mcp_extension.handlers import HandlerRegistry  # noqa: E402


class _Entity:
    def __init__(self, model, entity_id=None, **kwargs):
        self.id = int(entity_id) if entity_id is not None else 1
        self.name = kwargs.pop("name", f"entity-{self.id}")
        self.cardimage = kwargs.pop("cardimage", "TEST")
        for name, value in kwargs.items():
            setattr(self, name, value)


class Node(_Entity):
    def __init__(self, model, entity_id=None, **kwargs):
        if entity_id is None:
            entity_id = max((item.id for item in _Model.database.get(Node, [])), default=0) + 1
        super().__init__(model, entity_id, **kwargs)
        self.x = float(kwargs.get("x", entity_id))
        self.y = float(kwargs.get("y", 0.0))
        self.z = float(kwargs.get("z", 0.0))
        if model is not None and not any(
            item.id == self.id for item in _Model.database.get(Node, [])
        ):
            _Model.database.setdefault(Node, []).append(self)


class Element(_Entity):
    def __init__(self, model, entity_id=None, **kwargs):
        super().__init__(model, entity_id, **kwargs)
        self.config = int(kwargs.get("config", 104))
        self.type = int(kwargs.get("type", 1))
        self.volume = float(kwargs.get("volume", 1.0))
        self.jacobian = float(kwargs.get("jacobian", 0.9))
        self.aspect = float(kwargs.get("aspect", 1.2))
        self.skew = float(kwargs.get("skew", 0.1))


class _Generic(_Entity):
    def __init__(self, model, entity_id=None, **kwargs):
        entity_class = self.__class__
        if entity_id is None:
            entity_id = max(
                (item.id for item in _Model.database.get(entity_class, [])), default=0
            ) + 1
        super().__init__(model, entity_id, **kwargs)
        if model is not None and not any(
            item.id == self.id for item in _Model.database.get(entity_class, [])
        ):
            _Model.database.setdefault(entity_class, []).append(self)


class _ReturnStatus:
    def __init__(self, ok=True, message=""):
        self.status = 0 if ok else 1
        self.message = message
        self.ok = ok

    def __bool__(self):
        return self.ok


class _QueryResult:
    def __init__(self, **values):
        self.values = values

    @property
    def keys(self):
        return list(self.values)

    def __getitem__(self, key):
        return self.values[key]


class HmQueryResultList(list):
    pass


class _Session:
    def get_current_model(self):
        return "Model-1"

    def get_all_models(self):
        return ["Model-1"]

    def model_exists(self, name):
        return name == "Model-1"


class _Model:
    database = {
        Node: [Node(None, 1), Node(None, 2)],
        Element: [Element(None, 10)],
    }

    def __init__(self, name=None):
        self.name = name or "Model-1"

    def hm_getsolver(self):
        return "OptiStruct"

    def hm_gettotalmass(self):
        return [12.5]

    def hm_gettotalcog(self):
        return [1.0, 2.0, 3.0]

    def hm_getcurrentview(self):
        return [1.0] * 16

    def hm_getexistingentitytypes(self):
        return ["nodes", "elements"]

    def hm_getusermark(self, entity_type):
        return [1, 2]

    def createelements(self, config, type, integer_array):
        entity_id = max(
            (item.id for item in self.database.get(Element, [])), default=0
        ) + 1
        element = Element(self, entity_id, config=config, type=type)
        element.node_ids = [int(value) for value in integer_array]
        self.database.setdefault(Element, []).append(element)
        return {"status": "ok"}

    def _add_connection_element(self, config):
        entity_id = max(
            (item.id for item in self.database.get(Element, [])), default=0
        ) + 1
        element = Element(self, entity_id, config=config)
        self.database.setdefault(Element, []).append(element)
        return element

    def rigidlink(self, independent, collection, dofs):
        element = self._add_connection_element(55)
        element.independent_node_id = independent.id
        element.dependent_node_ids = [node.id for node in collection]
        element.dofs = dofs
        return {"status": "ok"}

    def rbe3(
        self,
        collection,
        independent_dofs,
        independent_weights,
        dependent_node,
        dof,
        weight,
    ):
        if dependent_node is None:
            nodes = list(collection)
            dependent_node = Node(
                self,
                x=sum(node.x for node in nodes) / len(nodes),
                y=sum(node.y for node in nodes) / len(nodes),
                z=sum(node.z for node in nodes) / len(nodes),
            )
        element = self._add_connection_element(56)
        element.dependent_node_id = dependent_node.id
        element.independent_node_ids = [node.id for node in collection]
        return {"status": "ok"}

    def weld(self, independent, dependent, length, systems, movenode):
        element = self._add_connection_element(5)
        element.node_ids = [independent.id, dependent.id]
        element.length = length
        element.systems = systems
        element.movenode = movenode
        return {"status": "ok"}

    def createspotweld(
        self, independent, dependent, length_given, length, systems,
        movenode, remesh, configval, property,
    ):
        element = self._add_connection_element(configval)
        element.node_ids = [independent.id, dependent.id]
        element.property_name = property
        return {"status": "ok"}

    def CE_ConnectorCreateByList(self, **kwargs):
        connector_class = sys.modules["hm.entities"].Connector
        connector = connector_class(self)
        connector.style = kwargs["ce_style"]
        connector.location_ids = [item.id for item in kwargs["entitylist"]]
        connector.link_ids = [item.id for item in kwargs["link_collection"]]
        self.last_connector = kwargs
        return {"status": "ok"}

    def currentcollector(self, entity_type, name):
        self.current_collector = (entity_type, name)
        return {"status": "ok"}

    def loadcreateonentity(self, **kwargs):
        load_class = sys.modules["hm.entities"].Load
        load_class(self, **kwargs)
        self.last_nodal_load = kwargs
        return {"status": "ok"}

    def pressuresonentity(self, **kwargs):
        self.last_pressure = kwargs
        return {"status": "ok"}

    def solidblock(self, **kwargs):
        solid_class = sys.modules["hm.entities"].Solid
        solid_class(self)
        self.last_solidblock = kwargs
        return {"status": "ok"}

    def solidcone(self, **kwargs):
        solid_class = sys.modules["hm.entities"].Solid
        solid_class(self)
        self.last_solidcone = kwargs
        return {"status": "ok"}

    def geomimport(self, translator_type, input_file_name, options):
        entities = sys.modules["hm.entities"]
        entities.Component(self)
        entities.Surface(self)
        entities.Solid(self)
        self.last_geomimport = (translator_type, input_file_name, options)
        return {"status": "ok"}

    def defaultmeshsurf_growth(self, **kwargs):
        entity_id = max(
            (item.id for item in self.database.get(Element, [])), default=0
        ) + 1
        self.database.setdefault(Element, []).append(Element(self, entity_id, config=104))
        self.last_automesh = kwargs
        return {"status": "ok"}

    def solidmap_solids_begin3(self, **kwargs):
        self.last_solidmap_begin3 = kwargs

    def solidmap_solids_begin(self, **kwargs):
        self.last_solidmap_begin = kwargs

    def solidmap_solids_end(self):
        entity_id = max(
            (item.id for item in self.database.get(Element, [])), default=0
        ) + 1
        self.database.setdefault(Element, []).append(
            Element(self, entity_id, config=208, volume=2.0)
        )
        return {"status": "ok"}

    def tetmesh(self, **kwargs):
        for _ in range(4):
            entity_id = max(
                (item.id for item in self.database.get(Element, [])), default=0
            ) + 1
            self.database.setdefault(Element, []).append(
                Element(self, entity_id, config=204, volume=0.5)
            )
        self.last_tetmesh = kwargs
        return {"status": "ok"}

    def element_smooth_nodes(self, **kwargs):
        for element in kwargs["elementsCollection"]:
            element.aspect = max(1.0, element.aspect * 0.8)
        self.last_smoothing = kwargs
        return {"status": "ok"}

    def elementqualityshutdown(self, dontsaveflag):
        return {"status": "ok", "dontsaveflag": dontsaveflag}

    def readfile(self, filename, load_cad_geometry_as_graphics):
        self.loaded_file = filename
        self.load_cad_geometry_as_graphics = load_cad_geometry_as_graphics
        return {"status": "ok"}

    def writefile(self, filename, do_not_write_facets):
        Path(filename).write_text("fake hm", encoding="utf-8")

    def hm_answernext(self, answer):
        return answer


class _CollectionItems(list):
    def __init__(self, model, entity_class, values):
        super().__init__(values)
        self.entity_class = entity_class

    def delete_items(self):
        deleted = {item.id for item in self}
        _Model.database[self.entity_class] = [
            item
            for item in _Model.database.get(self.entity_class, [])
            if item.id not in deleted
        ]
        if getattr(self.entity_class, "__name__", "") == "Component":
            # HyperMesh deletes a component's elements with the component.
            _Model.database[Element] = [
                item
                for item in _Model.database.get(Element, [])
                if int(getattr(getattr(item, "collector", None), "id", -1)) not in deleted
            ]
        return {"status": "ok"}


def _Collection(model, entity_class, ids=None, populate=True):
    values = list(_Model.database.get(entity_class, []))
    if ids is not None:
        wanted = {int(value) for value in ids}
        values = [item for item in values if item.id in wanted]
    return _CollectionItems(model, entity_class, values)


def _interactive(model, entity_class, message, highlight):
    return _Collection(model, entity_class)


def _add_rectangle(entities, hw, x0, y0, x1, y1, z=0.0):
    """Create a planar rectangular surface with four straight boundary lines."""
    corners = [(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)]
    edges = []
    for index in range(4):
        line = entities.Line(_Model())
        hw.line_points[line.id] = (corners[index], corners[(index + 1) % 4])
        edges.append(types.SimpleNamespace(id=line.id))
    return entities.Surface(
        _Model(), edges=edges, area=abs((x1 - x0) * (y1 - y0))
    )


def _add_cylinder_patch(entities, hw, radius, z0, z1, angle0, angle1):
    """Create a cylindrical patch with chord-approximated boundary lines."""
    start = math.radians(angle0)
    end = math.radians(angle1)
    corners = [
        (radius * math.cos(start), radius * math.sin(start), z0),
        (radius * math.cos(end), radius * math.sin(end), z0),
        (radius * math.cos(end), radius * math.sin(end), z1),
        (radius * math.cos(start), radius * math.sin(start), z1),
    ]
    edges = []
    for index in range(4):
        line = entities.Line(_Model())
        hw.line_points[line.id] = (corners[index], corners[(index + 1) % 4])
        edges.append(types.SimpleNamespace(id=line.id))
    surface = entities.Surface(
        _Model(),
        edges=edges,
        area=radius * (end - start) * abs(z1 - z0),
    )
    hw.surface_shapes[surface.id] = {
        "kind": "cylinder",
        "radius": radius,
        "z0": z0,
        "z1": z1,
        "angle0": angle0,
        "angle1": angle1,
    }
    return surface


class ExtensionHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_hm = sys.modules.get("hm")
        self.old_entities = sys.modules.get("hm.entities")
        self.old_hw = sys.modules.get("hw")
        self.old_hv = sys.modules.get("hw.hv")
        self.old_hwcont = sys.modules.get("hwcont")
        _Model.database = {
            Node: [Node(None, 1), Node(None, 2)],
            Element: [Element(None, 10)],
        }
        hm = types.ModuleType("hm")
        entities = types.ModuleType("hm.entities")
        hw = types.ModuleType("hw")
        hw.commands = []
        hw.surface_mark = []
        hw.mark2 = []
        hw.duplicate_ids = []
        hw.pair_distances = {}
        hw.intersecting_pairs = set()
        hw.line_points = {}
        hw.line_mark = []
        hw.surface_shapes = {}
        hw.free_edge_loops = ""
        hw.free_edge_loops_after = ""
        hw.free_edge_loop_states = []
        hw.free_edge_trim_no_change = False

        def _fake_eval_tcl(command):
            hw.commands.append(command)
            if command.startswith("*createmark surfs 1 "):
                tail = command[len("*createmark surfs 1 ") :].strip()
                surfaces = _Model.database.get(entities.Surface, [])
                if tail.strip('"') == "all":
                    hw.surface_mark = [item.id for item in surfaces]
                else:
                    hw.surface_mark = [
                        int(token) for token in tail.split() if token.isdigit()
                    ]
                return None
            if command.startswith("*createmark surfs 2 "):
                tail = command[len("*createmark surfs 2 ") :].strip()
                surfaces = _Model.database.get(entities.Surface, [])
                if tail.strip('"') == "all":
                    hw.mark2 = [item.id for item in surfaces]
                else:
                    hw.mark2 = [
                        int(token) for token in tail.split() if token.isdigit()
                    ]
                return None
            if command.startswith("*surfacemark_duplicate_check"):
                existing = {
                    item.id for item in _Model.database.get(entities.Surface, [])
                }
                matches = [
                    int(value)
                    for value in hw.duplicate_ids
                    if int(value) in existing
                ]
                tokens = command.split()
                options = tokens[3] if len(tokens) > 3 else "4"
                if options == "8":
                    hw.mark2 = [value for value in matches if value in hw.mark2]
                else:
                    hw.surface_mark = matches
                return None
            if command.strip() == "hm_getmark surfs 1":
                return " ".join(str(value) for value in hw.surface_mark)
            if command.strip() == "hm_getmark surfs 2":
                return " ".join(str(value) for value in hw.mark2)
            if command.startswith("hm_measureshortestdistance"):
                first = hw.surface_mark[0] if hw.surface_mark else None
                second = hw.mark2[0] if hw.mark2 else None
                distance = hw.pair_distances.get((first, second), 1.0e6)
                return f"{distance} 0 0 0 {first} 0 0 0 {second} 0 0 0"
            if command.startswith("*createmark lines 1 "):
                tail = command[len("*createmark lines 1 ") :].strip()
                if tail.strip('"') == "all":
                    hw.line_mark = sorted(hw.line_points)
                else:
                    hw.line_mark = [
                        int(token) for token in tail.split() if token.isdigit()
                    ]
                return None
            if command.strip() == "hm_getmark lines 1":
                return " ".join(str(value) for value in hw.line_mark)
            if command.startswith("hm_getcoordinatesofpointsonline"):
                tokens = command.split()
                line_id = int(tokens[1])
                points = hw.line_points.get(line_id, ())
                if len(points) < 2:
                    return ""
                start, end = points[0], points[1]
                params = [0.0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0]
                values = []
                for param in params:
                    x = start[0] + (end[0] - start[0]) * param
                    y = start[1] + (end[1] - start[1]) * param
                    z = start[2] + (end[2] - start[2]) * param
                    values.append(f"{x} {y} {z}")
                return " ".join(values)
            if command.startswith("hm_getsurfacenormalatcoordinate"):
                tokens = command.split()
                surface_id = int(tokens[1])
                x, y, z = (float(value) for value in tokens[2:5])
                shape = hw.surface_shapes.get(surface_id)
                if shape is None:
                    return f"{x} {y} {z} 0 0 1"
                if shape["kind"] == "cylinder":
                    angle = math.atan2(y, x)
                    start = math.radians(shape["angle0"])
                    end = math.radians(shape["angle1"])
                    angle = min(max(angle, start), end)
                    z_value = min(max(z, shape["z0"]), shape["z1"])
                    radius = shape["radius"]
                    return " ".join(
                        f"{value:.12g}"
                        for value in (
                            radius * math.cos(angle),
                            radius * math.sin(angle),
                            z_value,
                            math.cos(angle),
                            math.sin(angle),
                            0.0,
                        )
                    )
                return f"{x} {y} {z} 0 0 1"
            if command.startswith("*surfaceintersectmark"):
                key = (
                    hw.surface_mark[0] if hw.surface_mark else None,
                    hw.mark2[0] if hw.mark2 else None,
                )
                if key in hw.intersecting_pairs:
                    lines = _Model.database.setdefault(entities.Line, [])
                    next_id = max((item.id for item in lines), default=0) + 1
                    entities.Line(_Model(), next_id)
                return None
            if command.startswith("*deletemark surfs 1"):
                doomed = set(hw.surface_mark)
                _Model.database[entities.Surface] = [
                    item
                    for item in _Model.database.get(entities.Surface, [])
                    if item.id not in doomed
                ]
                return None
            if command.startswith("join [hm_getedgeloops"):
                return hw.free_edge_loops
            if command.startswith("*edgesmerge"):
                states = getattr(hw, "free_edge_loop_states", [])
                if states:
                    hw.free_edge_loops = states.pop(0)
                elif hw.free_edge_loops_after:
                    hw.free_edge_loops = hw.free_edge_loops_after
                return None
            if command.startswith("*createvector"):
                return None
            if command.startswith("*surfacemarksplitwithlines"):
                if getattr(hw, "free_edge_trim_no_change", False):
                    return None
                surfaces = [
                    item
                    for item in _Model.database.get(entities.Surface, [])
                    if item.id in hw.surface_mark
                ]
                if surfaces:
                    surfaces[0].area = (
                        float(getattr(surfaces[0], "area", 0.0)) * 0.5
                    )
                return None
            if command.startswith("*deletesolidswithelems"):
                marks = [
                    item
                    for item in hw.commands
                    if item.startswith("*createmark solids 1 ")
                ]
                if marks:
                    doomed = {int(value) for value in marks[-1].split()[3:]}
                    _Model.database[entities.Solid] = [
                        item
                        for item in _Model.database.get(entities.Solid, [])
                        if item.id not in doomed
                    ]

        hw.evalTcl = _fake_eval_tcl
        hm.Session = _Session
        hm.Model = _Model
        hm.Collection = _Collection
        hm.CollectionByInteractiveSelection = _interactive
        generated = {}
        for name in (
            "Component",
            "Property",
            "Loadcol",
            "Loadstep",
            "Set",
            "Surface",
            "Solid",
            "Point",
            "Line",
            "System",
            "Connector",
            "Assembly",
            "Constraint",
            "Contactbehavior",
            "Contactgroup",
            "Contactsurf",
            "Group",
            "Load",
        ):
            generated[name] = type(name, (_Generic,), {})
            setattr(entities, name, generated[name])
        entities.Material = type("Material", (_Generic,), {})
        entities.Node = Node
        entities.Element = Element
        hm.entities = entities
        hwcont = types.ModuleType("hwcont")

        class _IntList(list):
            def push_back(self, value):
                self.append(int(value))

        class _StringList(list):
            def push_back(self, value):
                self.append(str(value))

        class _DoubleList(list):
            def push_back(self, value):
                self.append(float(value))

        hwcont.hwIntList = _IntList
        hwcont.hwDoubleList = _DoubleList
        hwcont.hwStringList = _StringList
        sys.modules["hm"] = hm
        sys.modules["hm.entities"] = entities
        sys.modules["hw"] = hw
        sys.modules["hwcont"] = hwcont

    def tearDown(self) -> None:
        if self.old_hm is None:
            sys.modules.pop("hm", None)
        else:
            sys.modules["hm"] = self.old_hm
        if self.old_entities is None:
            sys.modules.pop("hm.entities", None)
        else:
            sys.modules["hm.entities"] = self.old_entities
        if self.old_hw is None:
            sys.modules.pop("hw", None)
        else:
            sys.modules["hw"] = self.old_hw
        if self.old_hv is None:
            sys.modules.pop("hw.hv", None)
        else:
            sys.modules["hw.hv"] = self.old_hv
        if self.old_hwcont is None:
            sys.modules.pop("hwcont", None)
        else:
            sys.modules["hwcont"] = self.old_hwcont

    def test_session_summary_and_entity_listing(self) -> None:
        registry = HandlerRegistry([])
        self.assertEqual(registry.get_session_info()["current_model"], "Model-1")
        summary = registry.get_model_summary(entity_types=["node", "element"])
        self.assertEqual(summary["counts"], {"node": 2, "element": 1})
        listed = registry.list_entities("node", limit=1)
        self.assertEqual(listed["total"], 2)
        self.assertEqual(listed["entities"][0]["x"], 1.0)

    def test_attribute_update_and_interactive_selection(self) -> None:
        registry = HandlerRegistry([])
        updated = registry.set_entity_attributes("node", 1, {"x": 9.0})
        self.assertEqual(updated["after"]["x"], 9.0)
        selected = registry.interactive_select("element")
        self.assertEqual(selected["ids"], [10])

    def test_model_metrics_unwraps_hypermesh_query_results(self) -> None:
        registry = HandlerRegistry([])
        model = _Model()
        model.hm_getsolver = lambda: (
            _ReturnStatus(),
            _QueryResult(value="Radioss"),
        )
        model.hm_gettotalmass = lambda: (
            _ReturnStatus(),
            _QueryResult(value=12.5),
        )
        model.hm_gettotalcog = lambda: (
            _ReturnStatus(),
            _QueryResult(x=1.0, y=2.0, z=3.0),
        )
        model.hm_getcurrentview = lambda: (
            _ReturnStatus(),
            _QueryResult(values=HmQueryResultList([1.0, 2.0])),
        )
        model.hm_getexistingentitytypes = lambda: (
            _ReturnStatus(),
            _QueryResult(values=HmQueryResultList(["nodes", "elements"])),
        )
        registry._model = lambda model_name=None: model

        metrics = registry.get_model_metrics()

        self.assertEqual(metrics["values"]["solver"], "Radioss")
        self.assertEqual(metrics["values"]["total_mass"], 12.5)
        self.assertEqual(
            metrics["values"]["center_of_gravity"],
            {"x": 1.0, "y": 2.0, "z": 3.0},
        )
        self.assertEqual(metrics["values"]["current_view"], [1.0, 2.0])
        self.assertEqual(
            metrics["values"]["existing_entity_types"],
            ["nodes", "elements"],
        )

    def test_controlled_live_creation_and_refresh(self) -> None:
        registry = HandlerRegistry([])
        nodes = registry.create_nodes([[1.5, 2.5, 3.5], [4.0, 5.0, 6.0]])
        self.assertEqual(nodes["ids"], [3, 4])
        elements = registry.create_elements([[1, 2, 3, 4]], config=104)
        self.assertEqual(elements["ids"], [11])
        material = registry.create_material(
            "Steel", cardimage="MAT1", values={"E": 210000.0, "Nu": 0.3}
        )
        self.assertEqual(material["material"]["name"], "Steel")
        self.assertEqual(material["material"]["E"], 210000.0)
        refreshed = registry.refresh_view(fit=True)
        self.assertEqual(refreshed["commands"], ["hm_viewfit", "hm_redraw"])

    def test_delete_solids_keeps_surfaces_and_validates_ids(self) -> None:
        entities = sys.modules["hm.entities"]
        solid = entities.Solid(_Model())
        surface = entities.Surface(_Model())
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            result = registry.delete_entities("solid", [solid.id])
        self.assertTrue(result["deleted"])
        self.assertEqual(result["counts_before"]["solid"], 1)
        self.assertEqual(result["counts_after"]["solid"], 0)
        self.assertEqual(result["counts_after"]["surface"], 1)
        self.assertEqual(
            [item.id for item in _Model.database[entities.Surface]], [surface.id]
        )
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            with self.assertRaisesRegex(ValueError, "do not exist"):
                registry.delete_entities("solid", [solid.id])
            with self.assertRaisesRegex(ValueError, "entity_type"):
                registry.delete_entities("node", [1])

    def test_delete_empty_components_and_refuse_occupied_components(self) -> None:
        entities = sys.modules["hm.entities"]
        surface = entities.Surface(_Model())
        empty = entities.Component(
            _Model(),
            name="empty",
            elements=[],
            surfaces=[],
            solids=[],
            lines=[],
            points=[],
        )
        occupied = entities.Component(
            _Model(),
            name="occupied",
            elements=[],
            surfaces=[surface],
            solids=[],
            lines=[],
            points=[],
        )
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            with self.assertRaisesRegex(ValueError, "still hold entities"):
                registry.delete_entities("component", [occupied.id])
            self.assertEqual(
                sorted(item.id for item in _Model.database[entities.Component]),
                sorted([empty.id, occupied.id]),
            )
            result = registry.delete_entities("component", [empty.id])
        self.assertTrue(result["deleted"])
        self.assertEqual(result["entity_type"], "component")
        self.assertEqual(result["counts_before"]["component"], 2)
        self.assertEqual(result["counts_after"]["component"], 1)
        self.assertEqual(result["counts_after"]["surface"], 1)
        self.assertEqual(result["counts_after"]["element"], 1)
        self.assertEqual(
            [item.id for item in _Model.database[entities.Component]],
            [occupied.id],
        )

    def test_reference_components_clear_with_their_elements(self) -> None:
        entities = sys.modules["hm.entities"]
        first = entities.Component(_Model(), name="S01")
        second = entities.Component(_Model(), name="S02")
        _Model.database[Element] = [
            Element(_Model(), 11, collector=first),
            Element(_Model(), 12, collector=second),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            result = registry.clear_reference_mesh(["S01", "S02"])
        self.assertTrue(result["cleared"])
        self.assertEqual(result["deleted_element_count"], 2)
        self.assertEqual(result["element_count_after"], 0)
        self.assertEqual(
            [item.name for item in _Model.database.get(entities.Component, [])],
            [],
        )
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            with self.assertRaisesRegex(ValueError, "do not exist"):
                registry.clear_reference_mesh(["S99"])
            with self.assertRaisesRegex(ValueError, "non-empty"):
                registry.clear_reference_mesh([])
            with self.assertRaisesRegex(ValueError, "Invalid component name"):
                registry.clear_reference_mesh(["bad name;"])

    def test_surface_repair_reports_duplicates_and_free_edges(self) -> None:
        entities = sys.modules["hm.entities"]
        hw = sys.modules["hw"]
        surfaces = [entities.Surface(_Model()) for _ in range(4)]
        hw.duplicate_ids = [surfaces[1].id]
        hw.free_edge_loops = "2 1 2 3 1;2 4 5 6 4"
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            report = registry.analyze_surface_repair(tolerance=0.1)
        self.assertEqual(report["surface_count"], 4)
        self.assertEqual(report["duplicate_count"], 1)
        self.assertEqual(report["duplicate_ids"], [surfaces[1].id])
        self.assertEqual(report["free_edge_loop_count"], 2)
        self.assertEqual(report["free_edge_count"], 6)

    def test_surface_repair_all_runs_three_detectors(self) -> None:
        entities = sys.modules["hm.entities"]
        hw = sys.modules["hw"]
        first = _add_rectangle(entities, hw, 0, 0, 10, 10)
        second = _add_rectangle(entities, hw, 5, 0, 15, 10)
        hw.pair_distances[(first.id, second.id)] = 0.0
        hw.duplicate_ids = []
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            report = registry.analyze_surface_repair_all(
                tolerance=0.1, samples_per_surface=8
            )
        self.assertEqual(report["duplicate_count"], 0)
        self.assertEqual(report["coplanar_overlap_group_count"], 1)
        self.assertEqual(report["curved_overlap_group_count"], 0)
        self.assertEqual(report["combined_overlap_group_count"], 1)
        self.assertEqual(
            report["combined_overlap_groups_sample"][0]["surface_count"], 2
        )
        self.assertIsNone(report["curved_error"])
        self.assertIn("coplanar", report)
        self.assertIn("curved", report)

    def test_duplicate_surface_removal_keeps_one_surface_per_set(self) -> None:
        entities = sys.modules["hm.entities"]
        hw = sys.modules["hw"]
        surfaces = [entities.Surface(_Model()) for _ in range(4)]
        hw.duplicate_ids = [surfaces[1].id]
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            removed = registry.remove_duplicate_surfaces(tolerance=0.1)
            remaining = {
                item.id for item in _Model.database.get(entities.Surface, [])
            }
        self.assertTrue(removed["removed"])
        self.assertEqual(removed["deleted_count"], 1)
        self.assertEqual(removed["surface_count_after"], 3)
        self.assertEqual(
            remaining, {surfaces[0].id, surfaces[2].id, surfaces[3].id}
        )
        self.assertTrue(removed["checkpoint"]["created"])
        self.assertEqual(removed["checkpoint"]["label"], "before_duplicate_surface_removal")

    def test_duplicate_surface_removal_is_noop_without_duplicates(self) -> None:
        entities = sys.modules["hm.entities"]
        hw = sys.modules["hw"]
        entities.Surface(_Model())
        hw.duplicate_ids = []
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            removed = registry.remove_duplicate_surfaces()
        self.assertFalse(removed["removed"])
        self.assertEqual(removed["deleted_count"], 0)
        self.assertIsNone(removed["checkpoint"])

    def test_free_edge_stitch_reports_reduction(self) -> None:
        entities = sys.modules["hm.entities"]
        hw = sys.modules["hw"]
        entities.Surface(_Model())
        entities.Surface(_Model())
        hw.free_edge_loops = "2 1 2 3 1;2 4 5 6 4"
        hw.free_edge_loops_after = "2 1 3 4 1"
        hw.line_points = {
            1: ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0)),
            2: ((0.0, 0.05, 0.0), (10.0, 0.05, 0.0)),
            3: ((0.0, 20.0, 0.0), (10.0, 20.0, 0.0)),
            4: ((0.0, 40.0, 0.0), (10.0, 40.0, 0.0)),
            5: ((0.0, 60.0, 0.0), (10.0, 60.0, 0.0)),
            6: ((0.0, 80.0, 0.0), (10.0, 80.0, 0.0)),
        }
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            stitched = registry.stitch_free_edges()
        self.assertTrue(stitched["stitched"])
        self.assertEqual(stitched["tolerance"], 0.2)
        self.assertEqual(stitched["surface_count"], 2)
        self.assertEqual(stitched["free_edge_count_before"], 6)
        self.assertEqual(stitched["free_edge_count_after"], 3)
        self.assertEqual(stitched["free_edges_reduced"], 3)
        self.assertEqual(stitched["merged_pair_count"], 1)
        self.assertEqual(stitched["merged_pairs_sample"][0]["edge_a"], 1)
        self.assertEqual(stitched["merged_pairs_sample"][0]["edge_b"], 2)
        self.assertEqual(stitched["merge_attempt_count"], 2)
        self.assertTrue(stitched["checkpoint"]["created"])
        self.assertTrue(
            any(command.startswith("*edgesmerge 1 2 ") for command in hw.commands)
        )

    def test_free_edge_stitch_merges_onto_non_free_edge(self) -> None:
        entities = sys.modules["hm.entities"]
        hw = sys.modules["hw"]
        entities.Surface(_Model())
        hw.free_edge_loops = "2 1 2 1"
        hw.free_edge_loops_after = "1 2"
        hw.line_points = {
            1: ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0)),
            2: ((0.0, 100.0, 0.0), (10.0, 100.0, 0.0)),
            3: ((0.0, 0.05, 0.0), (10.0, 0.05, 0.0)),
        }
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            stitched = registry.stitch_free_edges()
        self.assertTrue(stitched["stitched"])
        self.assertEqual(stitched["free_edge_count_before"], 2)
        self.assertEqual(stitched["free_edge_count_after"], 1)
        self.assertEqual(stitched["merged_pairs_sample"][0]["edge_a"], 1)
        self.assertEqual(stitched["merged_pairs_sample"][0]["edge_b"], 3)
        self.assertEqual(stitched["merge_attempt_count"], 1)
        self.assertTrue(
            any(command.startswith("*edgesmerge 1 3 ") for command in hw.commands)
        )

    def test_free_edge_stitch_stops_when_no_pair_merges(self) -> None:
        entities = sys.modules["hm.entities"]
        hw = sys.modules["hw"]
        entities.Surface(_Model())
        hw.free_edge_loops = "2 1 2 1"
        hw.line_points = {
            1: ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0)),
            2: ((0.0, 100.0, 0.0), (10.0, 100.0, 0.0)),
        }
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            stitched = registry.stitch_free_edges()
        self.assertFalse(stitched["stitched"])
        self.assertEqual(stitched["free_edge_count_before"], 2)
        self.assertEqual(stitched["free_edge_count_after"], 2)
        self.assertEqual(stitched["merge_attempt_count"], 0)
        self.assertEqual(stitched["merged_pair_count"], 0)
        self.assertTrue(stitched["checkpoint"]["created"])
        self.assertFalse(
            any(command.startswith("*edgesmerge") for command in hw.commands)
        )

    def test_free_edge_trim_reports_measured_change(self) -> None:
        entities = sys.modules["hm.entities"]
        hw = sys.modules["hw"]
        entities.Surface(_Model(), area=10.0)
        entities.Surface(_Model(), area=4.0)
        hw.free_edge_loops = "2 1 2 3 1"
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            trimmed = registry.trim_free_edges()
        self.assertTrue(trimmed["trimmed"])
        self.assertEqual(trimmed["operation"], "free_edges")
        self.assertEqual(trimmed["line_count"], 3)
        self.assertEqual(trimmed["surface_count"], 2)
        self.assertEqual(trimmed["free_edge_count_before"], 3)
        self.assertEqual(trimmed["free_edge_count_after"], 3)
        self.assertEqual(trimmed["projection"], "normal")
        self.assertTrue(trimmed["changed_surface_ids"])
        self.assertTrue(trimmed["checkpoint"]["created"])
        self.assertTrue(
            any(
                command.startswith("*surfacemarksplitwithlines 1 1 0 5")
                for command in hw.commands
            )
        )

    def test_free_edge_trim_rolls_back_without_change(self) -> None:
        entities = sys.modules["hm.entities"]
        hw = sys.modules["hw"]
        entities.Surface(_Model(), area=10.0)
        hw.free_edge_loops = "2 1 2 3 1"
        hw.free_edge_trim_no_change = True
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            with self.assertRaises(RuntimeError) as failure:
                registry.trim_free_edges()
            self.assertIn("no measurable change", str(failure.exception))
            self.assertIn("restored from checkpoint", str(failure.exception))

    def test_surface_intersection_detection_classifies_pairs(self) -> None:
        entities = sys.modules["hm.entities"]
        hw = sys.modules["hw"]
        shared = [types.SimpleNamespace(id=101)]
        surfaces = [
            entities.Surface(_Model(), edges=shared, area=10.0),
            entities.Surface(_Model(), edges=list(shared), area=10.0),
            entities.Surface(_Model(), area=4.0),
            entities.Surface(_Model(), area=4.0),
        ]
        first, second, third, fourth = (item.id for item in surfaces)
        hw.pair_distances[(third, fourth)] = 0.05
        hw.duplicate_ids = [fourth]
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            report = registry.analyze_surface_intersections(tolerance=0.1)
        self.assertEqual(report["surface_count"], 4)
        self.assertEqual(report["pair_count"], 6)
        self.assertEqual(report["adjacent_pair_count"], 1)
        self.assertEqual(report["adjacent_pairs_sample"][0]["surface_a"], first)
        self.assertEqual(report["adjacent_pairs_sample"][0]["shared_edge_count"], 1)
        self.assertEqual(report["adjacent_pairs_sample"][0]["area_a"], 10.0)
        self.assertEqual(report["identical_pairs_sample"][0]["area_b"], 4.0)
        self.assertEqual(report["separate_pair_count"], 4)
        self.assertEqual(report["candidate_pair_count"], 1)
        self.assertEqual(report["identical_pair_count"], 1)
        self.assertEqual(report["identical_pairs_sample"][0]["surface_b"], fourth)
        self.assertEqual(report["intersecting_pair_count"], 0)
        self.assertEqual(report["overlapping_pair_count"], 0)
        self.assertIsNone(report["checkpoint"])

    def test_surface_intersection_detection_marks_crossing_pairs(self) -> None:
        entities = sys.modules["hm.entities"]
        hw = sys.modules["hw"]
        first = entities.Surface(_Model())
        second = entities.Surface(_Model())
        hw.pair_distances[(first.id, second.id)] = 0.0
        hw.duplicate_ids = []
        hw.intersecting_pairs = {(first.id, second.id)}
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            report = registry.analyze_surface_intersections(tolerance=0.1)
            remaining_lines = _Model.database.get(entities.Line, [])
        self.assertEqual(report["intersecting_pair_count"], 1)
        self.assertEqual(
            report["intersecting_pairs_sample"][0]["created_line_count"], 1
        )
        self.assertEqual(report["overlapping_pair_count"], 0)
        self.assertTrue(report["checkpoint"]["created"])
        self.assertEqual(
            report["checkpoint"]["label"], "before_intersection_detection"
        )
        self.assertEqual(remaining_lines, [])

    def test_surface_intersection_detection_reports_coincident_pairs(self) -> None:
        entities = sys.modules["hm.entities"]
        hw = sys.modules["hw"]
        first = entities.Surface(_Model())
        second = entities.Surface(_Model())
        hw.pair_distances[(first.id, second.id)] = 0.0
        hw.duplicate_ids = []
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            report = registry.analyze_surface_intersections(tolerance=0.1)
        self.assertEqual(report["overlapping_pair_count"], 0)
        self.assertEqual(report["no_intersection_curve_pair_count"], 1)
        self.assertEqual(report["overlap_group_count"], 0)
        self.assertEqual(report["intersecting_pair_count"], 0)
        self.assertEqual(
            report["no_intersection_curve_pairs_sample"][0]["created_line_count"], 0
        )

    def test_surface_overlap_detection_groups_coplanar_chain(self) -> None:
        entities = sys.modules["hm.entities"]
        hw = sys.modules["hw"]
        first = _add_rectangle(entities, hw, 0, 0, 10, 10)
        second = _add_rectangle(entities, hw, 5, 0, 15, 10)
        third = _add_rectangle(entities, hw, 0, 5, 4, 15)
        hw.pair_distances[(first.id, second.id)] = 0.0
        hw.pair_distances[(first.id, third.id)] = 0.0
        hw.duplicate_ids = []
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            report = registry.analyze_surface_intersections(tolerance=0.1)
            self.assertEqual(report["overlapping_pair_count"], 2)
            self.assertEqual(report["contact_pair_count"], 0)
            self.assertEqual(report["overlap_group_count"], 1)
            group = report["overlap_groups_sample"][0]
            self.assertEqual(group["surface_count"], 3)
            self.assertEqual(
                group["surface_ids"], [first.id, second.id, third.id]
            )
            areas = {
                (pair["surface_a"], pair["surface_b"]): pair[
                    "overlap_area_estimate"
                ]
                for pair in report["overlapping_pairs_sample"]
            }
            self.assertGreater(areas[(first.id, second.id)], 40.0)
            self.assertLess(areas[(first.id, second.id)], 60.0)
            self.assertGreater(areas[(first.id, third.id)], 15.0)
            self.assertLess(areas[(first.id, third.id)], 25.0)

            review = registry.start_overlap_review()
            self.assertEqual(review["group_count"], 1)
            self.assertEqual(review["group"]["surface_count"], 3)
            colors = [
                command
                for command in hw.commands
                if command.startswith("*setreviewbymark lines 1 ")
            ]
            for expected in (
                "*setreviewbymark lines 1 3",
                "*setreviewbymark lines 1 4",
                "*setreviewbymark lines 1 5",
            ):
                self.assertIn(expected, colors)
            self.assertFalse(
                any(
                    command.startswith("*setreviewbymark surfs 1 ")
                    for command in hw.commands
                )
            )
            review = registry.next_overlap_review_group(
                decision="delete_some", delete_ids=[second.id]
            )
            self.assertTrue(review["completed"])
            self.assertEqual(review["pending_delete_ids"], [second.id])

    def test_curved_surface_overlap_detection_finds_partial_coincidence(self) -> None:
        entities = sys.modules["hm.entities"]
        hw = sys.modules["hw"]
        first = _add_cylinder_patch(entities, hw, 50.0, 0.0, 10.0, 0.0, 90.0)
        second = _add_cylinder_patch(entities, hw, 50.0, 0.0, 10.0, 45.0, 135.0)
        apart = _add_cylinder_patch(entities, hw, 60.0, 0.0, 10.0, 0.0, 90.0)

        def pair(surface_a, surface_b):
            return {
                "surface_a": surface_a.id,
                "surface_b": surface_b.id,
                "area_a": surface_a.area,
                "area_b": surface_b.area,
                "relation": "no_intersection_curve",
                "unverified_reason": "non_planar_surface",
            }

        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            with self.assertRaisesRegex(ValueError, "analyze_surface_intersections"):
                registry.analyze_curved_surface_overlaps()
            registry._last_surface_pairs = {
                "model_name": None,
                "tolerance": 0.1,
                "surface_ids": [first.id, second.id, apart.id],
                "pairs": [pair(first, second), pair(first, apart)],
            }
            report = registry.analyze_curved_surface_overlaps(
                samples_per_surface=16
            )
            with self.assertRaisesRegex(ValueError, "analyze_surface_intersections"):
                registry.start_overlap_review(source="all")
            review = registry.start_overlap_review(source="curved")
            self.assertTrue(review["active"])
            self.assertEqual(review["group_count"], 1)
            self.assertEqual(
                review["group"]["surface_ids"], [first.id, second.id]
            )
        self.assertEqual(report["evaluated_pair_count"], 2)
        self.assertEqual(report["partial_overlap_pair_count"], 1)
        self.assertEqual(report["contact_pair_count"], 1)
        self.assertEqual(report["separate_pair_count"], 0)
        overlap = report["overlap_pairs_sample"][0]
        self.assertEqual(overlap["relation"], "partial_overlap")
        self.assertGreater(overlap["coverage"], 0.3)
        self.assertLess(overlap["coverage"], 0.7)
        self.assertLess(overlap["normal_angle_degrees"], 1.0)
        self.assertEqual(report["overlap_group_count"], 1)
        self.assertEqual(
            report["overlap_groups_sample"][0]["surface_ids"],
            [first.id, second.id],
        )
        self.assertEqual(
            report["surface_sample_counts"][str(first.id)], 16
        )
        self.assertGreater(report["query_count"], 16)
        self.assertIsNotNone(report["milliseconds_per_query"])

    def test_surface_overlap_detection_reports_contact_without_area(self) -> None:
        entities = sys.modules["hm.entities"]
        hw = sys.modules["hw"]
        first = _add_rectangle(entities, hw, 0, 0, 10, 10)
        second = _add_rectangle(entities, hw, 10, 0, 20, 10)
        hw.pair_distances[(first.id, second.id)] = 0.0
        hw.duplicate_ids = []
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            report = registry.analyze_surface_intersections(tolerance=0.1)
            self.assertEqual(report["overlapping_pair_count"], 0)
            self.assertEqual(report["contact_pair_count"], 1)
            self.assertEqual(report["overlap_group_count"], 0)
            review = registry.start_surface_review()
            self.assertFalse(review["started"])
            review = registry.start_surface_review(relations=["contact"])
            self.assertEqual(review["group_count"], 1)
            self.assertEqual(
                review["group"]["relation"], "contact"
            )

    def test_overlap_review_validates_decisions(self) -> None:
        entities = sys.modules["hm.entities"]
        hw = sys.modules["hw"]
        first = _add_rectangle(entities, hw, 0, 0, 10, 10)
        second = _add_rectangle(entities, hw, 5, 0, 15, 10)
        hw.pair_distances[(first.id, second.id)] = 0.0
        hw.duplicate_ids = []
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            with self.assertRaisesRegex(ValueError, "start_overlap_review"):
                registry.start_overlap_review()
            registry.analyze_surface_intersections(tolerance=0.1)
            registry.start_overlap_review()
            with self.assertRaisesRegex(ValueError, "delete_ids must belong"):
                registry.next_overlap_review_group(
                    decision="delete_some", delete_ids=[999]
                )
            with self.assertRaisesRegex(ValueError, "delete_some requires"):
                registry.next_overlap_review_group(decision="delete_some")
            with self.assertRaisesRegex(ValueError, "only valid"):
                registry.next_overlap_review_group(
                    decision="keep_all", delete_ids=[second.id]
                )
            with self.assertRaisesRegex(ValueError, "decision must be one of"):
                registry.next_overlap_review_group(decision="bogus")
            with self.assertRaisesRegex(ValueError, "index must be in the range"):
                registry.show_overlap_review_group(index=9)

    def test_surface_review_walks_groups_and_records_decisions(self) -> None:
        entities = sys.modules["hm.entities"]
        hw = sys.modules["hw"]
        first = entities.Surface(_Model(), area=10.0)
        second = entities.Surface(_Model(), area=5.0)
        hw.pair_distances[(first.id, second.id)] = 0.0
        hw.duplicate_ids = []
        hw.intersecting_pairs = {(first.id, second.id)}
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            registry.analyze_surface_intersections(tolerance=0.1)
            review = registry.start_surface_review()
            self.assertEqual(review["group_count"], 1)
            self.assertTrue(review["active"])
            self.assertEqual(review["group_number"], 1)
            self.assertEqual(review["group"]["surface_a"], first.id)
            self.assertEqual(review["group"]["surface_b"], second.id)
            finished = registry.next_surface_review_group(decision="delete_b")
            self.assertTrue(finished["completed"])
            self.assertFalse(finished["active"])
            self.assertEqual(finished["pending_delete_ids"], [second.id])
            self.assertEqual(finished["decisions"][0]["decision"], "delete_b")
        self.assertTrue(
            any(command.startswith("*setreviewbymark") for command in hw.commands)
        )
        self.assertIn("*setreviewmode 0", hw.commands)

    def test_surface_review_requires_a_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            with self.assertRaisesRegex(ValueError, "analyze_surface_intersections"):
                registry.start_surface_review()

    def test_surface_review_rejects_invalid_inputs(self) -> None:
        entities = sys.modules["hm.entities"]
        hw = sys.modules["hw"]
        first = entities.Surface(_Model())
        second = entities.Surface(_Model())
        hw.pair_distances[(first.id, second.id)] = 0.0
        hw.duplicate_ids = []
        hw.intersecting_pairs = {(first.id, second.id)}
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            registry.analyze_surface_intersections(tolerance=0.1)
            with self.assertRaisesRegex(ValueError, "Unknown relations"):
                registry.start_surface_review(relations=["bogus"])
            registry.start_surface_review()
            with self.assertRaisesRegex(ValueError, "index must be in the range"):
                registry.show_surface_review_group(index=9)
            with self.assertRaisesRegex(ValueError, "decision must be one of"):
                registry.next_surface_review_group(decision="bogus")

    def test_surface_intersection_detection_rejects_large_pair_sets(self) -> None:
        entities = sys.modules["hm.entities"]
        for _ in range(4):
            entities.Surface(_Model())
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            with self.assertRaisesRegex(ValueError, "max_pairs"):
                registry.analyze_surface_intersections(max_pairs=2)
            with self.assertRaisesRegex(ValueError, "max_pairs must be an integer"):
                registry.analyze_surface_intersections(max_pairs=True)

    def test_surface_repair_rejects_invalid_inputs(self) -> None:
        entities = sys.modules["hm.entities"]
        entities.Surface(_Model())
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            with self.assertRaisesRegex(ValueError, "tolerance"):
                registry.analyze_surface_repair(tolerance=0.0)
            with self.assertRaisesRegex(ValueError, "do not exist"):
                registry.stitch_free_edges(surface_ids=[999])

    def test_creation_validation_and_controlled_model_load(self) -> None:
        registry = HandlerRegistry([])
        with self.assertRaisesRegex(ValueError, "finite"):
            registry.create_nodes([[float("nan"), 0.0, 0.0]])
        with self.assertRaisesRegex(ValueError, "do not exist"):
            registry.create_elements([[1, 999]], config=100)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "allowed"
            root.mkdir()
            model_file = root / "source.hm"
            model_file.write_text("fake hm", encoding="utf-8")
            registry = HandlerRegistry([str(root)])
            with self.assertRaisesRegex(ValueError, "replace_current=true"):
                registry.load_model(str(model_file))
            loaded = registry.load_model(str(model_file), replace_current=True)
            self.assertTrue(loaded["loaded"])

    def test_save_is_restricted_to_configured_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "allowed"
            root.mkdir()
            registry = HandlerRegistry([str(root)])
            result = registry.save_model(str(root / "model.hm"))
            self.assertTrue(Path(result["output_file"]).is_file())
            with self.assertRaisesRegex(ValueError, "outside"):
                registry.save_model(str(Path(tmp) / "outside.hm"))

    def test_geometry_cad_and_native_meshing_are_checkpointed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "allowed"
            root.mkdir()
            cad = root / "part.step"
            cad.write_text("fake step", encoding="utf-8")
            registry = HandlerRegistry([str(root)])

            block = registry.create_solid_block([0, 0, 0], [10, 20, 30])
            self.assertEqual(block["primitive"], "block")
            cylinder = registry.create_solid_cylinder([0, 0, 0], [1, 0, 0], 5, 20)
            self.assertEqual(cylinder["primitive"], "cylinder")
            imported = registry.import_cad(str(cad))
            self.assertEqual(imported["translator"], "step_ct")
            self.assertTrue(imported["surface_ids"])

            surface_mesh = registry.automesh_surfaces(
                imported["surface_ids"], element_size=2.0
            )
            self.assertEqual(surface_mesh["element_count"], 1)
            solid_mesh = registry.solid_map_mesh(
                imported["solid_ids"], element_size=2.0, element_type="quad"
            )
            self.assertEqual(solid_mesh["element_count"], 1)
            self.assertTrue(Path(block["checkpoint"]["checkpoint_file"]).is_file())

    def test_surface_mesh_algorithm_and_density_are_separate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "allowed"
            root.mkdir()
            cad = root / "part.step"
            cad.write_text("fake step", encoding="utf-8")
            registry = HandlerRegistry([str(root)])
            imported = registry.import_cad(str(cad))

            result = registry.automesh_surfaces(
                imported["surface_ids"],
                element_size=2.0,
                element_type="tria",
                density_mode="curvature",
                mesh_algorithm="free",
            )
            self.assertEqual(result["density_mode"], "curvature")
            self.assertEqual(result["mesh_algorithm"], "free")
            # Deprecated response alias still carries the density value.
            self.assertEqual(result["mesh_type"], "curvature")

            legacy = registry.automesh_surfaces(
                imported["surface_ids"],
                element_size=2.0,
                mesh_type="curvature",
            )
            self.assertEqual(legacy["density_mode"], "curvature")

            with self.assertRaisesRegex(ValueError, "Mapped"):
                registry.automesh_surfaces(
                    imported["surface_ids"],
                    element_size=2.0,
                    mesh_algorithm="mapped",
                )

    def test_meshing_returns_a_bounded_element_id_sample(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "allowed"
            root.mkdir()
            cad = root / "part.step"
            cad.write_text("fake step", encoding="utf-8")
            registry = HandlerRegistry([str(root)])
            imported = registry.import_cad(str(cad))

            original = handler_module.ID_SAMPLE_LIMIT
            handler_module.ID_SAMPLE_LIMIT = 0
            try:
                surface_mesh = registry.automesh_surfaces(
                    imported["surface_ids"], element_size=2.0
                )
                solid_mesh = registry.solid_map_mesh(
                    imported["solid_ids"], element_size=2.0, element_type="quad"
                )
            finally:
                handler_module.ID_SAMPLE_LIMIT = original

            for mesh in (surface_mesh, solid_mesh):
                self.assertEqual(mesh["element_count"], 1)
                self.assertEqual(mesh["element_ids"], [])
                self.assertTrue(mesh["element_ids_truncated"])

    def test_cylindrical_ogrid_quality_and_rollback_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "allowed"
            root.mkdir()
            registry = HandlerRegistry([str(root)])
            result = registry.create_cylindrical_ogrid(
                base_center=[0, 0, 0],
                axis=[1, 0, 0],
                length=10,
                radius=5,
                axial_divisions=1,
                circumferential_divisions=8,
                radial_layers=1,
                refresh=False,
            )
            self.assertEqual(result["node_count"], 34)
            self.assertEqual(result["penta6_count"], 8)
            self.assertEqual(result["hex8_count"], 8)
            self.assertEqual(result["quality"]["non_positive_volume_element_ids"], [])
            checkpoint = registry.create_checkpoint("manual test")
            with self.assertRaisesRegex(ValueError, "confirm=true"):
                registry.rollback_checkpoint(checkpoint["checkpoint_file"])
            rolled_back = registry.rollback_checkpoint(
                checkpoint["checkpoint_file"], confirm=True
            )
            self.assertTrue(rolled_back["rolled_back"])

    def test_new_modeling_validation_rejects_unsafe_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "allowed"
            root.mkdir()
            cad = root / "part.step"
            cad.write_text("fake step", encoding="utf-8")
            registry = HandlerRegistry([str(root)])
            with self.assertRaisesRegex(ValueError, "Invalid CAD import option"):
                registry.import_cad(str(cad), options=["foo=bar;bad"])
            with self.assertRaisesRegex(ValueError, "multiple of 4"):
                registry.create_cylindrical_ogrid(
                    [0, 0, 0], [1, 0, 0], 10, 5,
                    circumferential_divisions=10,
                )

    def test_connection_audit_and_checkpointed_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "allowed"
            root.mkdir()
            registry = HandlerRegistry([str(root)])
            registry.create_nodes(
                [[0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]]
            )

            audit = registry.get_connection_capabilities()
            self.assertEqual(audit["solver"], "OptiStruct")
            self.assertTrue(audit["exposed_operations"]["rbe3"])
            self.assertFalse(
                audit["audited_not_exposed"]["fastener"][
                    "active_solver_appears_supported"
                ]
            )
            safety = registry.get_safety_airbag_capabilities()
            self.assertEqual(safety["exposure_state"], "audit_only")
            self.assertEqual(safety["solver"], "OptiStruct")
            self.assertIn("airbag_folding", safety["required_validation_fixtures"])

            rigid = registry.create_rigid_link(1, [2, 3], refresh=False)
            self.assertEqual(rigid["connection_type"], "rigid_link")
            self.assertTrue(rigid["element_ids"])

            rbe3 = registry.create_rbe3([3, 4, 5, 6], refresh=False)
            self.assertEqual(rbe3["connection_type"], "rbe3")
            self.assertEqual(len(rbe3["auto_created_dependent_node_ids"]), 1)

            weld = registry.create_weld(1, 2, length=2.5, refresh=False)
            self.assertEqual(weld["connection_type"], "weld")
            self.assertTrue(weld["element_ids"])
            self.assertTrue(
                Path(weld["checkpoint"]["checkpoint_file"]).is_file()
            )

    def test_connection_validation_rejects_invalid_topology(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "allowed"
            root.mkdir()
            registry = HandlerRegistry([str(root)])
            with self.assertRaisesRegex(ValueError, "unique DOF"):
                registry.create_rigid_link(1, [2], dofs=112)
            with self.assertRaisesRegex(ValueError, "must not also"):
                registry.create_rigid_link(1, [1, 2])
            with self.assertRaisesRegex(ValueError, "six boolean"):
                registry.create_rbe3([1, 2], dependent_dofs=[True, False])
            with self.assertRaisesRegex(ValueError, "must be different"):
                registry.create_weld(1, 1)

    def test_solver_cards_loads_loadstep_and_spot_weld_are_checkpointed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            loadcol = registry.create_solver_card_entity(
                "loadcol", "FORCE", values={"color": 4}
            )
            loadcol_id = loadcol["entity"]["id"]
            prop = registry.create_solver_card_entity(
                "property", "PWELD", cardimage="PWELD", values={"D": 5.0}
            )
            force = registry.create_nodal_load(
                [1, 2], "force", [10.0, 0.0, 0.0], loadcol_id=loadcol_id
            )
            step = registry.create_loadstep(
                "STEP", "OS_TYPE", 1, load_attribute="OS_LOADID",
                loadcol_id=loadcol_id
            )
            weld = registry.create_spot_weld(
                1, 2, 21, "PWELD", refresh=False
            )
        self.assertEqual(prop["entity"]["name"], "PWELD")
        self.assertEqual(force["load_kind"], "force")
        self.assertEqual(step["entity_type"], "loadstep")
        self.assertTrue(weld["element_ids"])

    def test_connector_intent_is_created_but_not_claimed_as_realized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            line_class = sys.modules["hm.entities"].Line
            component_class = sys.modules["hm.entities"].Component
            line = line_class(_Model())
            first_component = component_class(_Model())
            second_component = component_class(_Model())
            connector = registry.create_connector(
                "line",
                [line.id],
                "seam",
                "component",
                [first_component.id, second_component.id],
                num_links=2,
                tolerance=5.0,
                refresh=False,
            )
        self.assertTrue(connector["connector_ids"])
        self.assertFalse(connector["realized"])
        self.assertEqual(connector["style"], "seam")

    def test_connector_validation_rejects_unsupported_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            with self.assertRaisesRegex(ValueError, "node.*line"):
                registry.create_connector(
                    "element", [10], "spot", "component", [1], refresh=False
                )

    def test_tetra_mesh_and_quality_repair_return_before_after_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = HandlerRegistry([tmp])
            solid_class = sys.modules["hm.entities"].Solid
            solid = solid_class(_Model())
            meshed = registry.tetra_mesh_solids(
                [solid.id], element_size=2.0, refresh=False
            )
            repaired = registry.repair_mesh_quality(
                meshed["element_ids"], iterations=2, refresh=False
            )
        self.assertEqual(meshed["method"], "native_tetmesh")
        self.assertEqual(meshed["element_count"], 4)
        self.assertTrue(repaired["repaired"])
        self.assertIn("quality_before", repaired)
        self.assertIn("quality_after", repaired)

    def test_hyperview_postprocess_returns_evidence_and_unpacks_query(self) -> None:
        class Page:
            def __init__(self, layout=1, title=""):
                self.layout = layout
                self.title = title
                self.id = 2

        class Window:
            def addModelAndResult(self, model, result):
                self.loaded = (model, result)

            def fit(self):
                pass

            def draw(self):
                pass

        class HVModel:
            pass

        class Result:
            def __init__(self):
                self.subcase = 1
                self.simulation = 1

            def getSubcaseIds(self):
                return [1]

            def getSubcaseLabels(self):
                return ["Linear Static"]

            def getDataTypes(self, subcase):
                return ["Displacement", "Stress"]

            def getDataComponents(self, data_type, subcase):
                return ["Mag", "X", "Y", "Z"]

            def getSimulationIds(self, subcase):
                return [1]

            def plot(self, scalar, waitTillLoaded=False):
                self.scalar = scalar

        class LegendScalar:
            minValue = 0.0
            maxValue = 1.25

        class Entity:
            pass

        class Collection:
            def __init__(self, entity_type, model=None):
                self.entity_type = entity_type

            def getSize(self):
                return 3

        class QueryResultsTool:
            def __init__(self, **kwargs):
                self.collection = None

            def setDataSourceQuery(self, query):
                self.source = query

            def getDataSourceQuery(self):
                return self.source

            def query(self):
                return [[1, 0.25], [2, 1.25], [3, 0.5], [4, float("nan")]]

        class ResultDefinitionScalar:
            def __init__(self, **kwargs):
                self.values = kwargs

        window = Window()
        result = Result()
        legend = LegendScalar()
        model = HVModel()

        class Session:
            def setActive(self, tag, page=None):
                self.page = page

            def get(self, tag, **kwargs):
                return {
                    Window: window,
                    Result: result,
                    LegendScalar: legend,
                    HVModel: model,
                }[tag]

        class CaptureImageTool:
            def __init__(self, **kwargs):
                self.file = None

            def capture(self):
                Path(self.file).write_bytes(b"fake png")

        hw = types.ModuleType("hw")
        hv = types.ModuleType("hw.hv")
        hw.Page = Page
        hw.Window = Window
        hw.Session = Session
        hw.CaptureImageTool = CaptureImageTool
        hw.hv = hv
        hv.Result = Result
        hv.LegendScalar = LegendScalar
        hv.Model = HVModel
        hv.Node = Entity
        hv.Element = Entity
        hv.Collection = Collection
        hv.QueryResultsTool = QueryResultsTool
        hv.ResultDefinitionScalar = ResultDefinitionScalar
        sys.modules["hw"] = hw
        sys.modules["hw.hv"] = hv

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_file = root / "case.fem"
            result_file = root / "case.h3d"
            image_file = root / "output" / "displacement.png"
            model_file.write_text("deck", encoding="utf-8")
            result_file.write_bytes(b"h3d")
            registry = HandlerRegistry([str(root)])
            post = registry.postprocess_hyperview_result(
                str(model_file), str(result_file), str(image_file),
                simulation="first", average_mode="simple"
            )

        self.assertTrue(post["postprocessed"])
        self.assertEqual(post["bridge_version"], "0.29.0")
        self.assertEqual(post["legend"]["maximum"], 1.25)
        self.assertEqual(post["query"]["maximum_row"], [2, 1.25])
        self.assertEqual(post["query"]["minimum_row"], [1, 0.25])
        self.assertEqual(post["query"]["row_count"], 4)
        self.assertEqual(post["selected_simulation"], 1)
        self.assertEqual(post["contour"]["average_mode"], "simple")
        self.assertEqual(result.scalar.averageMode, "simple")
        self.assertGreater(post["screenshot"]["size_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
