import weave

PROJECT = "maorblumberg-tel-aviv-university/communication-agent"
BATCH_SIZE = 100

TARGET_ROOT_OP = "communication_agent.inline_preview"

client = weave.init(PROJECT)


def extract_base_op_name(op_name: str) -> str:
    """
    Convert:
    weave:///entity/project/op/communication_agent.inline_preview:HASH

    Into:
    communication_agent.inline_preview
    """
    op_name = str(op_name or "")

    if "/op/" in op_name:
        op_name = op_name.split("/op/", 1)[1]

    if ":" in op_name:
        op_name = op_name.split(":", 1)[0]

    return op_name


to_delete = []

calls = client.get_calls(
    columns=["op_name", "started_at"],
    sort_by=[{"field": "started_at", "direction": "desc"}],
)

for call in calls:
    raw_op_name = getattr(call, "op_name", "")
    base_op_name = extract_base_op_name(raw_op_name)

    # Root rows only, not nested node/tool/summary rows.
    if base_op_name == TARGET_ROOT_OP:
        to_delete.append(call.id)

print(f"Found {len(to_delete)} root '{TARGET_ROOT_OP}' calls")

print("\nPreview first 20 call IDs:")
for call_id in to_delete[:20]:
    print(call_id)

confirm = input("\nType DELETE to delete these root calls: ").strip().upper()

print(f"DEBUG confirm={confirm!r}")
if confirm == "DELETE":
    for i in range(0, len(to_delete), BATCH_SIZE):
        batch = to_delete[i : i + BATCH_SIZE]
        client.delete_calls(batch)
        print(f"Deleted {i + len(batch)} / {len(to_delete)}")

    print("Done.")
else:
    print("Cancelled.")