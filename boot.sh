#!/bin/bash

PLAN=${PLAN:-c4-m10}
STORAGE=${STORAGE:-30G}
# Map of pre-release series codename to its feature freeze date (YYYY-MM-DD).
# A pre-release series ("Active Development" / "Pre-release Freeze") is only
# tested once its feature freeze has passed. See the release schedule, e.g.
# https://documentation.ubuntu.com/release-notes/27.04/schedule/
FEATURE_FREEZE_DATES=${FEATURE_FREEZE_DATES:-'{"tenacious":"2027-02-25"}'}
RELEASES=${RELEASES:-$(curl -s https://api.launchpad.net/devel/ubuntu/series | \
        jq -r --argjson feature_freeze "$FEATURE_FREEZE_DATES" \
              --arg today "$(date +%Y-%m-%d)" '
            .entries[]
            | select(.version >= "22.04")
            | select(
                (.status == "Supported" or .status == "Current Stable Release")
                or ($feature_freeze[.name] != null
                    and $feature_freeze[.name] <= $today)
              )
            | .name')}
PROPOSED=${APT_POCKETS:-"false true"}
PPA=${APT_PPAS:-"false ppa:ubuntu-security-proposed/ppa"}
PPA_PRIORITY=${APT_PPA_PRIORITY:-500}
for codename in $RELEASES; do
    for proposed in $PROPOSED; do
        for ppa in $PPA; do
            name=ubuntu-$codename
            if [ "$proposed" != false ]; then
                name+=-proposed
            fi
            if [ "$ppa" != false ]; then
                name+=-ppa
            fi

            cat << EOSYSTEM >> systems.$$
      - ${name}:
          image: ubuntu-daily:${codename}
          environment:
            APT_ENABLE_PROPOSED: ${proposed}
            APT_ENABLE_PPA: ${ppa}
            APT_PPA_PRIORITY: ${PPA_PRIORITY}
EOSYSTEM
        done
    done
done
cp spread.yaml.in spread.yaml.$$
sed -i "s/@@SPREAD_PLAN@@/${PLAN}/" spread.yaml.$$
sed -i "s/@@SPREAD_STORAGE@@/${STORAGE}/" spread.yaml.$$
sed -i '/@@SPREAD_SYSTEMS@@/{
       s/@@SPREAD_SYSTEMS@@//g
       r /dev/stdin
       }' spread.yaml.$$ < systems.$$
rm -f systems.$$
mv spread.yaml.$$ spread.yaml
