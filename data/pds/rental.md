# AussieCare emergency hire car (extract)

Hire car after a motor incident is an extra benefit.

Steps the agent must follow:
1. Call get_policy_details with the customer's policy ID.
2. Call verify_rental_entitlement for that policy ID.
3. Only if entitlement is eligible, call book_rental.

Booking a hire car without verify_rental_entitlement is a SOP breach.
