#!/usr/bin/env bash
# Container start command for the goliath-worker RunPod template.
# Paste the bash -lc line below into the template, as one command.
# The line downloads deploy/runpod/goliath-worker-bootstrap.sh from the
# newest non-draft, non-prerelease goliathomics-* release (or
# GOLIATH_RELEASE_TAG) and runs it.
# On failure it execs /start.sh so sshd and Jupyter stay up.
bash -lc 'set +x;python3 -c "import os,json,base64,re,urllib.request as u;e=os.environ.get;t=e(\"GH_TOKEN\") or e(\"GITHUB_TOKEN\") or \"\";r=e(\"GOLIATH_GITHUB_REPO\") or \"Goliath-Research/GoliathOmics\";g=e(\"GOLIATH_RELEASE_TAG\") or \"\";a=(e(\"GOLIATH_GITHUB_API\") or \"https://api.github.com\").rstrip(\"/\");assert t and chr(10) not in t and chr(13) not in t and re.fullmatch(\"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+\",r);h={\"Authorization\":\"Bearer \"+t};q=lambda n: json.load(u.urlopen(u.Request(a+n,headers=h),timeout=60));g=g or next((i[\"tag_name\"] for i in q(\"/repos/\"+r+\"/releases?per_page=100\") if isinstance(i,dict) and not i.get(\"draft\") and not i.get(\"prerelease\") and str(i.get(\"tag_name\") or \"\").startswith(\"goliathomics-\")), \"\");assert re.fullmatch(\"goliathomics-[A-Za-z0-9._+-]+\",g);b=base64.b64decode(q(\"/repos/\"+r+\"/contents/deploy/runpod/goliath-worker-bootstrap.sh?ref=\"+g)[\"content\"]);assert b\"goliath-worker\" in b;open(\"/tmp/goliath-worker-bootstrap.sh\",\"wb\").write(b)"&&bash /tmp/goliath-worker-bootstrap.sh "$@"||{ echo goliath-stub-failed >&2;exec "${GOLIATH_START_KEEPALIVE:-/start.sh}";}'
