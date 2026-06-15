"""Direct test of evolution generation logic (clustering + skill update).

This bypasses the observation extraction step and directly tests:
1. Instinct clustering by normalized_trigger
2. Target skill inference
3. SKILL.md generation/update
4. Evolution log creation (active vs proposed)

Usage:
    uv run python scripts/test_evolution_direct.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database import Database
from src.evolution_log import EvolutionLogStore
from src.instinct_extractor import InstinctStore
from src.skill_manager import SkillManager


async def test_evolution_direct():
    """Directly test evolution generation from existing instincts."""
    test_data_root = Path("data/test-evolution")
    db_path = test_data_root / "web-agent.db"

    if not db_path.exists():
        print(f"Error: Test database not found at {db_path}")
        print("Please run: uv run python scripts/generate_evolution_test_data.py")
        return

    print("=" * 60)
    print("Testing Evolution Generation (Direct)")
    print(f"Database: {db_path}")
    print("=" * 60)

    # Initialize components
    db = Database(db_path=db_path)
    await db.init()

    instinct_store = InstinctStore(db)
    evolution_store = EvolutionLogStore(db)
    skill_manager = SkillManager(db)

    # Show initial state
    print("\n[Initial State]")
    instincts = await instinct_store.get_active()
    print(f"  Active instincts: {len(instincts)}")

    evolutions_before = await evolution_store.list_logs()
    print(f"  Existing evolutions: {evolutions_before['total']}")

    # Group instincts by normalized_trigger and domain
    print("\n[Cluster Analysis]")
    clusters_by_domain: dict[str, dict[str, list]] = {}

    for inst in instincts:
        domain = inst["domain"]
        norm_trigger = inst["normalized_trigger"]

        if domain not in clusters_by_domain:
            clusters_by_domain[domain] = {}
        if norm_trigger not in clusters_by_domain[domain]:
            clusters_by_domain[domain][norm_trigger] = []

        clusters_by_domain[domain][norm_trigger].append(inst)

    # Process each domain
    result = {"clusters": 0, "applied": 0, "proposed": 0, "skipped": 0}

    for domain, clusters in clusters_by_domain.items():
        print(f"\n  Domain: {domain}")

        for norm_trigger, cluster in clusters.items():
            avg_confidence = sum(i["confidence"] for i in cluster) / len(cluster)

            print(f"\n    Cluster: {norm_trigger}")
            print(f"      • Instincts: {len(cluster)}")
            print(f"      • Avg confidence: {avg_confidence:.2f}")

            # Check minimum requirements
            if len(cluster) < 2:
                print(f"      ⚠️  Skipped: < 2 instincts")
                result["skipped"] += 1
                continue

            if avg_confidence < 0.5:
                print(f"      ⚠️  Skipped: avg confidence < 0.5")
                result["skipped"] += 1
                continue

            result["clusters"] += 1

            # Determine target skill (using first available skill for testing)
            skills_dir = test_data_root / "shared-skills"
            target_skill = ""
            if skills_dir.exists():
                for skill_dir in skills_dir.iterdir():
                    if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
                        target_skill = skill_dir.name
                        break

            if not target_skill:
                print(f"      ⚠️  Skipped: no target skill found")
                result["skipped"] += 1
                continue

            print(f"      • Target skill: {target_skill}")

            # Read current skill content
            skill_file = skills_dir / target_skill / "SKILL.md"
            current_skill = skill_file.read_text() if skill_file.exists() else ""

            # Simulate LLM response for testing (since we may not have API key)
            # In production, this would call the LLM with GENERATION_PROMPT
            api_key = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY", "")

            if api_key:
                # Real LLM call
                import httpx
                from src.instinct_extractor import ANTHROPIC_MESSAGES_URL, GENERATION_PROMPT, _EXTRACTION_MODEL
                from src.block_processor import process_content_blocks, strip_thinking_blocks
                from src.text_utils import strip_markdown_fences

                gen_prompt = GENERATION_PROMPT.format(
                    skill_name=target_skill,
                    current_skill=current_skill,
                    instincts="\n".join(
                        f"- [{i['normalized_trigger']}] {i['trigger']} → {i['action']}"
                        for i in cluster
                    ),
                )

                try:
                    async with httpx.AsyncClient() as client:
                        gen_resp = await client.post(
                            ANTHROPIC_MESSAGES_URL,
                            headers={
                                "x-api-key": api_key,
                                "anthropic-version": "2023-06-01",
                                "content-type": "application/json",
                            },
                            json={
                                "model": _EXTRACTION_MODEL,
                                "max_tokens": 4000,
                                "messages": [{"role": "user", "content": gen_prompt}],
                            },
                            timeout=180.0,
                        )
                        gen_resp.raise_for_status()
                        gen_data = gen_resp.json()
                        content_blocks = gen_data.get("content", [])
                        new_content = process_content_blocks(content_blocks, lambda _: None)
                        new_content = strip_thinking_blocks(new_content)
                        new_content = strip_markdown_fences(new_content)

                    print(f"      • LLM generated new SKILL.md ({len(new_content)} chars)")
                except Exception as e:
                    print(f"      ⚠️  LLM call failed: {e}")
                    print(f"      → Using simulated response for testing")
                    new_content = current_skill + "\n\n## Added by Evolution\n"
                    for inst in cluster:
                        new_content += f"- **{inst['normalized_trigger']}**: {inst['action']}\n"
            else:
                # Simulated response for testing without API key
                new_content = current_skill + "\n\n## Added by Evolution\n"
                for inst in cluster:
                    new_content += f"- **{inst['normalized_trigger']}**: {inst['action']}\n"
                print(f"      • Simulated SKILL.md update (no API key)")

            # Apply or propose based on confidence
            instinct_ids = [i["id"] for i in cluster]

            if avg_confidence >= 0.7:
                # Auto-apply: archive old version, write new, create evolution log
                import shutil
                import time

                skill_dir = skills_dir / target_skill
                versions_dir = skill_dir / "versions"
                versions_dir.mkdir(exist_ok=True)

                # Archive current version
                version_num = len(list(versions_dir.glob("v*"))) + 1
                archive_path = versions_dir / f"v{version_num}"
                shutil.copy2(skill_file, archive_path)
                print(f"      • Archived to: {archive_path.name}")

                # Write new content
                skill_file.write_text(new_content)
                print(f"      • Updated SKILL.md")

                # Compute baseline metrics
                cutoff = time.time() - 7 * 86400
                async with db.connection() as conn:
                    rows = await conn.execute_fetchall(
                        """SELECT success, COUNT(*) as cnt FROM observations
                           WHERE created_at >= ? AND event_type = 'tool_call_end'
                           AND success IS NOT NULL
                           GROUP BY success""",
                        (cutoff,),
                    )
                total = sum(r[1] for r in rows)
                if total > 0:
                    success_count = sum(r[1] for r in rows if r[0] == 1)
                    tool_success_rate = success_count / total
                else:
                    tool_success_rate = 0.5  # Default

                composite = 0.5 * tool_success_rate + 0.3 * 1.0 + 0.2 * 0.5

                # Create evolution log
                evo_result = await evolution_store.create_log(
                    skill_name=target_skill,
                    from_version=f"v{version_num - 1}",
                    to_version=f"v{version_num}",
                    source="evolution_test",
                    evolve_reason=f"Cluster: {norm_trigger} ({len(cluster)} instincts, avg_conf={avg_confidence:.2f})",
                    proposed_content=new_content,
                    baseline_composite=round(composite, 4),
                    baseline_metrics=json.dumps({
                        "tool_success_rate": round(tool_success_rate, 4),
                        "session_success_rate": 1.0,
                        "daily_usage": total,
                    }),
                    status="active",
                )
                evolution_id = evo_result["id"]

                # Link instincts to evolution
                await instinct_store.link_to_evolution(instinct_ids, evolution_id)

                print(f"      ✓ AUTO-APPLIED (evolution #{evolution_id})")
                result["applied"] += 1
            else:
                # Propose for review
                evo_result = await evolution_store.create_log(
                    skill_name=target_skill,
                    from_version="current",
                    to_version="proposed",
                    source="evolution_test",
                    evolve_reason=f"Cluster: {norm_trigger} ({len(cluster)} instincts, avg_conf={avg_confidence:.2f})",
                    proposed_content=new_content,
                    status="proposed",
                )
                evolution_id = evo_result["id"]

                # Link instincts to evolution
                await instinct_store.link_to_evolution(instinct_ids, evolution_id)

                print(f"      → PROPOSED (evolution #{evolution_id})")
                result["proposed"] += 1

    # Commit changes
    async with db.connection() as conn:
        await conn.commit()

    # Show final state
    print("\n" + "=" * 60)
    print("Results")
    print("=" * 60)
    print(f"  Clusters processed: {result['clusters']}")
    print(f"  Auto-applied: {result['applied']}")
    print(f"  Proposed: {result['proposed']}")
    print(f"  Skipped: {result['skipped']}")

    evolutions_after = await evolution_store.list_logs()
    print(f"\n  Total evolutions in DB: {evolutions_after['total']}")

    for evo in evolutions_after["items"]:
        print(f"\n  Evolution #{evo['id']}:")
        print(f"    • Skill: {evo['skill_name']}")
        print(f"    • Status: {evo['status']}")
        print(f"    • Trigger: {evo.get('trigger_description', 'N/A')[:60]}...")
        if evo.get("composite_score") is not None:
            print(f"    • Baseline score: {evo['composite_score']}")

    # Check skill file
    skill_file = test_data_root / "shared-skills" / "test-coding-patterns" / "SKILL.md"
    versions_dir = test_data_root / "shared-skills" / "test-coding-patterns" / "versions"

    print(f"\n[Skill File Status]")
    print(f"  Current skill: {skill_file}")
    if skill_file.exists():
        content = skill_file.read_text()
        print(f"  Content length: {len(content)} chars")
        if "Added by Evolution" in content:
            print(f"  ✓ Contains evolution additions")

    if versions_dir.exists():
        versions = sorted(versions_dir.glob("v*"))
        print(f"  Archived versions: {len(versions)}")
        for v in versions:
            print(f"    • {v.name}")

    print("\n" + "=" * 60)
    print("Test complete!")
    print("=" * 60)

    await db.close()


if __name__ == "__main__":
    asyncio.run(test_evolution_direct())
