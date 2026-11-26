# Full-stack Yoda Hybrid (Option C) Salt state.
#
# Full install or minor upgrade:
#   salt 'yoda*' state.apply yoda
#
# Targeted security patches (minimal blast radius):
#   salt 'yoda*' state.apply yoda.web      # Roll web-tier containers only (~30s)
#   salt 'yoda*' state.apply yoda.edge     # Roll Caddy/Anubis/Redis only
#   salt 'yoda*' state.apply yoda.data     # Upgrade packaged yoda-ruleset + yoda-migrate

include:
  - yoda.prereqs
  - yoda.config
  - yoda.data
  - yoda.web
  - yoda.edge
