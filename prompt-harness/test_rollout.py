# -*- coding: utf-8 -*-
"""隔离测试：rollout append-only 事件 + 倒扫 + 续跑定位（不起服务器、不调 LLM）。"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "prompt_harness"))
import rollout


def main():
    tmp = Path(tempfile.mkdtemp(prefix="rollout_test_"))
    try:
        book = tmp / "book"
        # ① 追加事件 + 文件生成
        rid = rollout.new_run_id()
        assert len(rid) == 12
        rollout.append_event(book, rid, "run_started", kind="batch_generate",
                             data={"target_chapters": 10})
        rollout.append_event(book, rid, "step_completed", kind="batch_generate",
                             step="l2 完成", chapter=1, data={"done": 1})
        rollout.append_event(book, rid, "step_completed", kind="batch_generate",
                             step="第1章 落盘", chapter=1)
        p = rollout._run_path(book, rid)
        assert p.is_file()
        lines = p.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3
        assert json.loads(lines[0])["event"] == "run_started"
        print("① append + 文件 OK")

        # ② 倒扫：最后一条在前
        events = list(rollout.reverse_scan(p, max_records=0))
        assert events[0]["event"] == "step_completed"
        assert events[-1]["event"] == "run_started"
        print("② reverse_scan 全量 OK:", len(events))

        # ③ 倒扫限流：只要最后 2 条
        last2 = list(rollout.reverse_scan(p, max_records=2))
        assert len(last2) == 2 and last2[0]["step"] == "第1章 落盘"
        print("③ reverse_scan max_records OK")

        # ④ load_run_meta：stale_running=True（没终态）
        meta = rollout.load_run_meta(book, rid)
        assert meta["kind"] == "batch_generate"
        assert meta["params"]["target_chapters"] == 10
        assert meta["last_event"] == "step_completed"
        assert meta["stale_running"] is True
        print("④ meta + stale_running OK")

        # ⑤ 写终态后 stale_running=False
        rollout.append_event(book, rid, "run_completed", kind="batch_generate",
                             data={"chapters": 10})
        meta = rollout.load_run_meta(book, rid)
        assert meta["last_event"] == "run_completed"
        assert meta["stale_running"] is False
        print("⑤ 终态识别 OK")

        # ⑥ find_resumable：另一个未结束的 run 能被找到
        rid2 = rollout.new_run_id()
        rollout.append_event(book, rid2, "run_started", kind="batch_generate",
                             data={"target_chapters": 5})
        rollout.append_event(book, rid2, "step_completed", step="l2", kind="batch_generate")
        res = rollout.find_resumable(book, "batch_generate")
        assert res is not None and res["run_id"] == rid2, res
        print("⑥ find_resumable OK:", res["run_id"])

        # ⑦ list_runs 按 mtime 倒序，两个 run 都在
        runs = rollout.list_runs(book)
        ids = {r["run_id"] for r in runs}
        assert {rid, rid2} <= ids, ids
        print("⑦ list_runs OK:", len(runs))

        # ⑧ 损坏行容错：手动写半截坏行，倒扫不炸
        with open(p, "a", encoding="utf-8") as fh:
            fh.write("{this is broken json\n")
        events = list(rollout.reverse_scan(p))
        assert events[0]["event"] == "run_completed"  # 坏行被跳过
        print("⑧ 损坏行容错 OK")

        # ⑨ 不存在的 run
        assert rollout.load_run_meta(book, "nope") is None
        assert list(rollout.reverse_scan(book / "nope.jsonl")) == []
        print("⑨ 缺失文件容错 OK")

        # ⑩ 事件写入失败静默（不可写路径）不抛
        rollout.append_event("/nonexistent_root_xyz/???", rid, "run_started")
        print("⑩ 写入失败静默 OK")

        print("ALL PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
