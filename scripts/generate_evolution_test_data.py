"""Generate test data in a SEPARATE database for evolution pipeline testing.

This creates a test database at data/test-evolution.db that can be used
to verify the evolution generation logic without affecting the main database.

Usage:
    uv run python scripts/generate_evolution_test_data.py

Then to test the evolution pipeline with this database:
    DATA_ROOT=data/test-evolution uv run python -c "
import asyncio
from pathlib import Path
from src.database import Database
from src.observation import ObservationStore
from src.instinct_extractor import InstinctStore, InstinctExtractor
from src.evolution_log import EvolutionLogStore
from src.skill_manager import SkillManager

async def test():
    db = Database(db_path=Path('data/test-evolution/web-agent.db'))
    await db.init()
    obs = ObservationStore(db)
    inst_store = InstinctStore(db)
    evo_store = EvolutionLogStore(db)
    skill_mgr = SkillManager(db, 'data/test-evolution')

    extractor = InstinctExtractor(
        db=db, obs_store=obs, instinct_store=inst_store,
        evolution_store=evo_store, skill_manager=skill_mgr,
        data_root='data/test-evolution'
    )
    result = await extractor.run_once(force=True)
    print('Result:', result)

asyncio.run(test())
"
"""

import asyncio
import os
import shutil
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database import Database
from src.instinct_extractor import InstinctStore


async def generate_test_data():
    """Generate test data in a separate test database."""
    # Use a separate directory for test data
    test_data_root = Path("data/test-evolution")
    test_data_root.mkdir(parents=True, exist_ok=True)

    db_path = test_data_root / "web-agent.db"

    # Remove existing test database to start fresh
    if db_path.exists():
        db_path.unlink()
    for wal_file in db_path.parent.glob("web-agent.db*"):
        wal_file.unlink()

    db = Database(db_path=db_path)
    await db.init()

    instinct_store = InstinctStore(db)

    print("=" * 60)
    print("Generating test data for evolution pipeline")
    print(f"Database: {db_path}")
    print("=" * 60)

    # Define test instinct clusters
    test_clusters = [
        {
            "name": "grep_before_edit",
            "domain": "tool_usage",
            "normalized_trigger": "grep-before-edit",
            "instincts": [
                {
                    "trigger": "Before editing a file, search for the target text to ensure accuracy",
                    "action": "Always run Grep before Edit to locate exact text positions",
                    "confidence": 0.75,
                },
                {
                    "trigger": "When making code changes, verify the context first",
                    "action": "Use Grep to confirm the text exists before attempting Edit",
                    "confidence": 0.80,
                },
                {
                    "trigger": "Avoid Edit failures due to incorrect text assumptions",
                    "action": "Pre-validate edit targets with Grep searches",
                    "confidence": 0.72,
                },
            ],
        },
        {
            "name": "large_file_handling",
            "domain": "tool_usage",
            "normalized_trigger": "large-file-read",
            "instincts": [
                {
                    "trigger": "When reading files larger than 500 lines, use offset/limit",
                    "action": "Use Read with offset and limit parameters for large files",
                    "confidence": 0.78,
                },
                {
                    "trigger": "Avoid loading entire large files into context",
                    "action": "Read specific sections of large files instead of entire content",
                    "confidence": 0.82,
                },
            ],
        },
        {
            "name": "bash_error_handling",
            "domain": "tool_usage",
            "normalized_trigger": "bash-error-check",
            "instincts": [
                {
                    "trigger": "After Bash command failures, check error output",
                    "action": "Always examine stderr when Bash commands fail",
                    "confidence": 0.65,
                },
                {
                    "trigger": "When commands fail, diagnose before retrying",
                    "action": "Analyze error messages to understand root cause before retry",
                    "confidence": 0.60,
                },
                {
                    "trigger": "Avoid blind retries of failed commands",
                    "action": "Check exit codes and error output before attempting fixes",
                    "confidence": 0.68,
                },
            ],
        },
        {
            "name": "task_orchestration_pattern",
            "domain": "task_orchestration",
            "normalized_trigger": "multi-step-planning",
            "instincts": [
                {
                    "trigger": "For complex tasks, break down into subtasks first",
                    "action": "Use TodoWrite to plan multi-step tasks before execution",
                    "confidence": 0.73,
                },
                {
                    "trigger": "Track progress on long-running tasks",
                    "action": "Update task status as work progresses through stages",
                    "confidence": 0.70,
                },
            ],
        },
    ]

    # Insert instincts
    print("\n[1/3] Inserting test instincts...")
    inserted_ids = []

    for cluster in test_clusters:
        print(f"\n  Cluster: {cluster['name']} (domain={cluster['domain']})")

        for inst_data in cluster["instincts"]:
            instinct_id = await instinct_store.upsert(
                domain=cluster["domain"],
                normalized_trigger=cluster["normalized_trigger"],
                trigger=inst_data["trigger"],
                action=inst_data["action"],
                confidence=inst_data["confidence"],
                evidence_json='{"test_data": true}',
            )
            inserted_ids.append(instinct_id)
            print(f"    ✓ Instinct #{instinct_id}: confidence={inst_data['confidence']}")

        avg_conf = sum(i["confidence"] for i in cluster["instincts"]) / len(cluster["instincts"])
        action = "AUTO-APPLY" if avg_conf >= 0.7 else "PROPOSE"
        print(f"    → Cluster avg: {avg_conf:.2f} → {action}")

    print(f"\n  Total instincts inserted: {len(inserted_ids)}")

    # Commit the changes
    async with db.connection() as conn:
        await conn.commit()
    print("  ✓ Changes committed to database")

    # Verify
    print("\n[2/3] Verifying inserted instincts...")
    active_instincts = await instinct_store.get_active()
    print(f"  Active instincts in DB: {len(active_instincts)}")

    clusters: dict[str, list] = {}
    for inst in active_instincts:
        clusters.setdefault(inst["normalized_trigger"], []).append(inst)

    print("\n  Clusters:")
    for norm_trigger, items in clusters.items():
        avg_conf = sum(i["confidence"] for i in items) / len(items)
        will_apply = "✓ AUTO-APPLY" if avg_conf >= 0.7 else "→ PROPOSE"
        print(f"    • {norm_trigger}: {len(items)} instincts, avg={avg_conf:.2f} {will_apply}")

    # Create sample shared skill
    print("\n[3/3] Creating sample shared skill...")
    skills_dir = test_data_root / "shared-skills"
    test_skill_dir = skills_dir / "test-coding-patterns"
    test_skill_dir.mkdir(parents=True, exist_ok=True)

    skill_content = """# Test Coding Patterns

This is a test skill for verifying evolution generation.

## Current Patterns

### Error Handling
- Always check return values
- Log errors with context

### Code Style
- Use consistent naming conventions
- Keep functions small and focused

## Testing
- Write tests for new functionality
- Verify edge cases
"""

    skill_file = test_skill_dir / "SKILL.md"
    skill_file.write_text(skill_content)
    print(f"  ✓ Created skill at: {skill_file}")

    # Summary
    print("\n" + "=" * 60)
    print("Test data generation complete!")
    print("=" * 60)
    print(f"""
Test database: {db_path}

To run the evolution pipeline with this test data:

    DATA_ROOT=data/test-evolution uv run python -c "
import asyncio
from pathlib import Path
from src.database import Database
from src.observation import ObservationStore
from src.instinct_extractor import InstinctStore, InstinctExtractor
from src.evolution_log import EvolutionLogStore
from src.skill_manager import SkillManager

async def test():
    db = Database(db_path=Path('data/test-evolution/web-agent.db'))
    await db.init()
    obs = ObservationStore(db)
    inst_store = InstinctStore(db)
    evo_store = EvolutionLogStore(db)
    skill_mgr = SkillManager(db, 'data/test-evolution')

    extractor = InstinctExtractor(
        db=db, obs_store=obs, instinct_store=inst_store,
        evolution_store=evo_store, skill_manager=skill_mgr,
        data_root='data/test-evolution'
    )
    result = await extractor.run_once(force=True)
    print('Evolution result:', result)

    # Check results
    evolutions = await evo_store.list_evolutions()
    print(f'Created {{len(evolutions[\"items\"])}} evolution(s)')

asyncio.run(test())
"

Expected results:
- grep-before-edit: avg=0.76 → AUTO-APPLY
- large-file-read: avg=0.80 → AUTO-APPLY
- bash-error-check: avg=0.64 → PROPOSE
- multi-step-planning: avg=0.72 → AUTO-APPLY
""")

    await db.close()


if __name__ == "__main__":
    asyncio.run(generate_test_data())
