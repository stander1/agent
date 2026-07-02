from __future__ import annotations

import unittest
from collections import namedtuple
from dataclasses import dataclass

from pydantic import BaseModel

from agent_runtime.drivers.autogen import (
    _clone_message_with_text_field,
    _core_message_text_field,
    _extract_core_embedded_prompt_view,
    _extract_core_rewrite_wire_envelope,
    _extract_core_message_argument,
    _replace_core_message_argument,
    _state_refs_from_wire_envelope,
)


@dataclass(frozen=True)
class FrozenCorePayload:
    source: str
    content: str
    payload_kind: str = "core_transport_state"


class MutableCorePayload:
    def __init__(self, text: str, tag: str) -> None:
        self.text = text
        self.tag = tag


NamedCorePayload = namedtuple("NamedCorePayload", ["body", "payload_kind"])


class PydanticCorePayload(BaseModel):
    content: str
    payload_kind: str = "core_transport_state"


class AutoGenCoreRewriteHelperTest(unittest.TestCase):
    def test_detects_dict_body_field(self) -> None:
        field_name, value = _core_message_text_field({"body": "native payload"})

        self.assertEqual(field_name, "body")
        self.assertEqual(value, "native payload")

    def test_clones_frozen_dataclass_without_changing_type(self) -> None:
        native = FrozenCorePayload(source="user", content="long native text")

        cloned = _clone_message_with_text_field(
            native,
            field_name="content",
            content="AGENTLITE_CORE_CONTENT_REWRITE v1",
        )

        self.assertIsInstance(cloned, FrozenCorePayload)
        self.assertEqual(cloned.source, "user")
        self.assertEqual(cloned.payload_kind, "core_transport_state")
        self.assertEqual(cloned.content, "AGENTLITE_CORE_CONTENT_REWRITE v1")
        self.assertEqual(native.content, "long native text")

    def test_clones_mutable_object_text_field(self) -> None:
        native = MutableCorePayload(text="native text", tag="keep")

        cloned = _clone_message_with_text_field(
            native,
            field_name="text",
            content="rewritten text",
        )

        self.assertIsInstance(cloned, MutableCorePayload)
        self.assertIsNot(cloned, native)
        self.assertEqual(cloned.text, "rewritten text")
        self.assertEqual(cloned.tag, "keep")
        self.assertEqual(native.text, "native text")

    def test_clones_namedtuple_body_field(self) -> None:
        native = NamedCorePayload(
            body="native body",
            payload_kind="core_transport_state",
        )

        cloned = _clone_message_with_text_field(
            native,
            field_name="body",
            content="rewritten body",
        )

        self.assertIsInstance(cloned, NamedCorePayload)
        self.assertEqual(cloned.body, "rewritten body")
        self.assertEqual(cloned.payload_kind, "core_transport_state")
        self.assertEqual(native.body, "native body")

    def test_clones_pydantic_content_field(self) -> None:
        native = PydanticCorePayload(content="native content")

        cloned = _clone_message_with_text_field(
            native,
            field_name="content",
            content="rewritten content",
        )

        self.assertIsInstance(cloned, PydanticCorePayload)
        self.assertEqual(cloned.content, "rewritten content")
        self.assertEqual(cloned.payload_kind, "core_transport_state")
        self.assertEqual(native.content, "native content")

    def test_clones_dict_body_field(self) -> None:
        native = {"body": "native body", "payload_kind": "dict_state"}

        cloned = _clone_message_with_text_field(
            native,
            field_name="body",
            content="rewritten body",
        )

        self.assertIsInstance(cloned, dict)
        self.assertEqual(cloned["body"], "rewritten body")
        self.assertEqual(cloned["payload_kind"], "dict_state")
        self.assertEqual(native["body"], "native body")

    def test_replaces_core_message_positional_argument(self) -> None:
        native = FrozenCorePayload(source="user", content="native")
        replacement = FrozenCorePayload(source="user", content="rewrite")

        message, source = _extract_core_message_argument((native, "receiver"), {})
        new_args, new_kwargs = _replace_core_message_argument(
            (native, "receiver"),
            {},
            source=source,
            replacement=replacement,
        )

        self.assertIs(message, native)
        self.assertEqual(new_args[0], replacement)
        self.assertEqual(new_args[1], "receiver")
        self.assertEqual(new_kwargs, {})

    def test_extracts_wire_state_refs_and_embedded_prompt_view(self) -> None:
        content = (
            "AGENTLITE_CORE_CONTENT_REWRITE v1\n"
            "shp_wire={\"state_refs\":[{\"state_id\":\"state_1\","
            "\"state_type\":\"artifact_state\",\"version\":2,"
            "\"payload_kind\":\"structured_non_text\","
            "\"contains_embedding_refs\":false,"
            "\"usage_hint\":\"artifact_summary\",\"tier\":\"cold\"}]}\n"
            "prompt_view:\n"
            "[artifact_state:state_1] summary"
        )

        wire = _extract_core_rewrite_wire_envelope(content)
        refs = _state_refs_from_wire_envelope(wire)
        prompt_view = _extract_core_embedded_prompt_view(content)

        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].state_id, "state_1")
        self.assertEqual(refs[0].version, 2)
        self.assertEqual(refs[0].tier, "cold")
        self.assertEqual(prompt_view, "[artifact_state:state_1] summary")


if __name__ == "__main__":
    unittest.main()
