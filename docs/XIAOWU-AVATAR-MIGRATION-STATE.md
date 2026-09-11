# Xiaowu Avatar migration state

The namespace migration ran on 2026-09-11 and preserved a timestamped backup. No legacy file was removed during this audit.

## Safe to keep

| Reference | Reason |
|---|---|
| yinyue2 and yinyue2-local | Active production LLM alias and Hermes provider |
| yinyue2-hindsight and yinyue2-embedding | Active memory model aliases |
| yinyue_cosplay01 and Krea2_YINYUE workflow names | Active remote workflow compatibility identifiers |
| yinyue-prefixed output filenames | Stable artifact naming; latest revision 20 uses it |
| CSS classes with yinyue prefix | Internal dashboard selectors |
| temporary-directory prefixes and test fixture names | Internal, non-user-facing compatibility |

## Compatibility required

| Reference | Reason |
|---|---|
| /yinyue_avatar command alias | Router and visual plugin accept it alongside /xiaowu-avatar |
| Remote workflow paths containing YINYUE | The deployed 3060 file is verified under that name |
| Manifest workflow_id containing YINYUE | Matches the deployed workflow contract |

## Safe to remove later

Only after a separate change window and recovery test:

- timestamped pre-migration copies inside active skill directories;
- old yinyue-avatar state after retention and rollback requirements expire;
- migration backup after an approved backup retention period;
- stale bytecode and old workflow backup variants;
- historical test documentation that still points to the retired state root.

This audit did not delete any of them.

## Unknown

- Whether all external callers have migrated from /yinyue_avatar.
- Whether the 5090 host has any unpublished consumer of legacy workflow names.
- Whether older transaction recovery code still expects the legacy state root.
- Whether the qwen-worker mirror divergence is intentional.

## Changes observed on 2026-09-11

The root skill name and heading changed from yinyue-avatar to xiaowu-avatar; the exposed tool changed to xiaowu_avatar_generate; context/resend paths changed to the Xiaowu skill root. The visual plugin and router were renamed, router route key changed, Hermes enabled-plugin entries changed, Identity Studio state authority moved under the xiaowu namespace, and a migration backup was created. A post-migration revision 20 execution completed and delivered.
