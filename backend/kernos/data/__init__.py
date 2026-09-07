"""``kernos.data``: the data plane (design §5.3) — collections as content, one documents
table, and the generated tools a profile enables with ``tool_packs: [{pack: "collections"}]``."""
from kernos.data.schema import (  # noqa: F401
    DOC_ID_RE, SLUG_RE, SUPPORTED_KEYWORDS, SchemaError, check_collection_schema, check_schema,
)
from kernos.data.store import FIND_LIMIT, MAX_DOCUMENTS, DataStore, generated_tool_names  # noqa: F401
from kernos.data.pack import CollectionsPack, tools_for  # noqa: F401
