---
name: claim-policy
description: Checks an expense claim against the travel, meals and equipment policy before it is submitted, and says which rule a rejected claim broke.
license: Apache-2.0
metadata:
  owner: finance-ops
---

# Claim policy

1. Identify the expense category from the claim: travel, meals or equipment.
2. Read `references/limits.md` for the category's per-claim limit.
3. Entertainment and gifts are never claimable; return the claim and say so.
4. A claim above its category limit is returned with the limit quoted.
5. A claim within policy goes on to check_cost_centre_funds.
