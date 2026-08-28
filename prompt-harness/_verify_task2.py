# -*- coding: utf-8 -*-
"""Task 2 verification: fragments.json CRUD."""
from prompt_harness.ai_creation import load_fragments, save_fragments, add_fragment, update_fragment, delete_fragment
from pathlib import Path
import tempfile, os

with tempfile.TemporaryDirectory() as tmp:
    os.makedirs(os.path.join(tmp, '.ainovel'))

    # 1. load empty
    frags = load_fragments(tmp)
    assert frags == [], f"Expected empty, got {frags}"
    print("1. load_fragments empty: OK")

    # 2. add_fragment
    f1 = add_fragment(tmp, "a1", "detail", "A dark alley scene", scene_idx=0, beat_idx=[0, 1])
    assert f1["arc_id"] == "a1"
    assert f1["type"] == "detail"
    assert f1["content"] == "A dark alley scene"
    assert f1["scene_idx"] == 0
    assert f1["beat_idx"] == [0, 1]
    assert f1["id"].startswith("f")
    print("2. add_fragment: OK (id=%s)" % f1["id"])

    # 3. load after add
    frags = load_fragments(tmp)
    assert len(frags) == 1
    print("3. load_fragments after add: OK")

    # 4. add second fragment
    f2 = add_fragment(tmp, "a1", "dialogue", "Hello there", scene_idx=1, blanks=["b1"], fixed=False)
    frags = load_fragments(tmp)
    assert len(frags) == 2
    print("4. add second fragment: OK (id=%s)" % f2["id"])

    # 5. update_fragment
    ok = update_fragment(tmp, f1["id"], content="Updated alley scene", scene_idx=2)
    assert ok is True
    frags = load_fragments(tmp)
    assert frags[0]["content"] == "Updated alley scene"
    assert frags[0]["scene_idx"] == 2
    print("5. update_fragment: OK")

    # 6. update non-existent
    ok = update_fragment(tmp, "nonexistent", content="x")
    assert ok is False
    print("6. update_fragment non-existent: OK")

    # 7. delete_fragment
    ok = delete_fragment(tmp, f2["id"])
    assert ok is True
    frags = load_fragments(tmp)
    assert len(frags) == 1
    print("7. delete_fragment: OK")

    # 8. delete non-existent
    ok = delete_fragment(tmp, "nonexistent")
    assert ok is False
    print("8. delete_fragment non-existent: OK")

    # 9. save_fragments direct
    save_fragments(tmp, [{"id": "f99", "test": True}])
    frags = load_fragments(tmp)
    assert len(frags) == 1 and frags[0]["id"] == "f99"
    print("9. save_fragments direct: OK")

print()
print("All 9 verification tests PASSED")
