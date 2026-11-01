---
name: claim-policy-hotel
description: Checks an expense claim against the travel, meals, hotel and equipment policy before it is submitted, and says which rule a rejected claim broke.
---

# Hotel claim policy

1. A hotel night above 180 dollars (250 in a capital city) is returned with the
   limit quoted.
2. Minibar and room-service charges are never claimable; return the claim and
   say which line broke the rule.
3. A hotel claim needs the folio, not just the card slip.
4. A claim within policy goes on to submit_claim.
