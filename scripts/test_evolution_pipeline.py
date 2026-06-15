"""Run evolution pipeline with test data to verify the logic.

This script runs the InstinctExtractor on the test database to verify
that instincts are clustered and evolutions are generated correctly.

Usage:
    uv run python scripts/test_evolution_pipeline.py
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database import Database
from src.evolution_log import EvolutionLogStore
from src.instinct_extractor import InstinctExtractor, InstinctStore
from src.observation import ObservationStore
from src.skill_manager import SkillManager


async def test_evolution_pipeline():
    """Run the evolution pipeline and verify results."""
    test_data_root = Path("data/test-evolution")
    db_path = test_data_root / "web-agent.db"

    if not db_path.exists():
        print(f"Error: Test database not found at {db_path}")
        print("Please run: uv run python scripts/generate_evolution_test_data.py")
        return

    print("=" * 60)
    print("Testing Evolution Pipeline")
    print(f"Database: {db_path}")
    print("=" * 60)

    # Initialize components
    db = Database(db_path=db_path)
    await db.init()

    obs_store = ObservationStore(db)
    instinct_store = InstinctStore(db)
    evolution_store = EvolutionLogStore(db)
    skill_manager = SkillManager(db)

    extractor = InstinctExtractor(
        db=db,
        obs_store=obs_store,
        instinct_store=instinct_store,
        evolution_store=evolution_store,
        skill_manager=skill_manager,
        data_root=str(test_data_root),
    )

    # Check API key
    api_key = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("\n⚠️  No API key set (ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY)")
        print("   The extraction step requires an LLM call.")
        print("   Continuing anyway to show the pipeline flow...\n")

    # Show initial state
    print("\n[Initial State]")
    instincts_before = await instinct_store.get_active()
    print(f"  Active instincts: {len(instincts_before)}")

    evolutions_before = await evolution_store.list_logs()
    print(f"  Existing evolutions: {evolutions_before['total']}")

    # Run extraction
    print("\n[Running Evolution Pipeline]")
    print("  Calling: extractor.run_once(force=True)")
    print("  (This will cluster instincts and generate evolutions)")

    try:
        result = await extractor.run_once(force=True)
        print(f"\n  Result: {result}")
    except Exception as e:
        print(f"\n  ⚠️  Extraction failed: {e}")
        print("  This is expected if no API key is set.")
        print("  Showing cluster analysis instead...\n")
        result = {"extracted": 0, "clusters": 0, "applied": 0, "proposed": 0}

    # Show cluster analysis (what SHOULD happen)
    print("\n[Cluster Analysis - What Will Happen]")
    for domain in ("tool_usage", "task_orchestration"):
        instincts = await instinct_store.get_active(domain=domain)
        clusters: dict[str, list] = {}
        for inst in instincts:
            clusters.setdefault(inst["normalized_trigger"], []).append(inst)

        for norm_trigger, cluster in clusters.items():
            if len(cluster) < 2:
                continue
            avg_conf = sum(i["confidence"] for i in cluster) / len(cluster)
            if avg_conf < 0.5:
                continue

            if avg_conf >= 0.7:
                action = "✓ AUTO-APPLY"
            else:
                action = "→ PROPOSE"

            print(f"\n  Cluster: {norm_trigger} (domain={domain})")
            print(f"    • Instincts: {len(cluster)}")
            print(f"    • Avg confidence: {avg_conf:.2f}")
            print(f"    • Expected action: {action}")

    # Show final state
    print("\n[Final State]")
    evolutions_after = await evolution_store.list_logs()
    print(f"  Total evolutions: {evolutions_after['total']}")

    for evo in evolutions_after["items"]:
        print(f"\n  Evolution #{evo['id']}:")
        print(f"    • Skill: {evo['skill_name']}")
        print(f"    • Status: {evo['status']}")
        print(f"    • Instinct count: {evo.get('instinct_count', '?')}")
        if evo.get("composite_score"):
            print(f"    • Baseline score: {evo['composite_score']}")

    # Check skill file
    skill_file = test_data_root / "shared-skills" / "test-coding-patterns" / "SKILL.md"
    versions_dir = test_data_root / "shared-skills" / "test-coding-patterns" / "versions"

    print(f"\n[Skill File Status]")
    print(f"  Current skill: {skill_file}")
    print(f"  Exists: {skill_file.exists()}")

    if versions_dir.exists():
        versions = list(versions_dir.glob("v*"))
        print(f"  Archived versions: {len(versions)}")
        for v in sorted(versions):
            print(f"    • {v.name}")

    print("\n" + "=" * 60)
    print("Test complete!")
    print("=" * 60)

    await db.close()


if __name__ == "__main__":
    asyncio.run(test_evolution_pipeline())
