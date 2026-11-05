# Former Render host

This deployment branch preserves the old Basket URL by temporarily redirecting requests to the verified Azure deployment. Caddy serves the redirect without starting the API or opening the former Neon database. The full app and deployment tooling remain on `codex/oracle-free-migration-20261104`.

Before cutover, verify the Azure HTTPS endpoint. Then deploy this branch, stop the staged Azure app for the database copy, and activate Azure only after row-count verification. The original Render release is commit `57f2645`; restoring it requires reversing the database cutover once Azure accepts writes.
