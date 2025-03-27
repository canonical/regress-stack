#!/bin/bash

PLAN=${PLAN:-c4-m8}
STORAGE=${STORAGE:-15G}
RELEASES=${RELEASES:-$(curl -s https://api.launchpad.net/devel/ubuntu/series | \
        jq -r '.entries[] | select(.version >= "22.04" and
                               (.status == "Supported" or
                                .status == "Current Stable Release" or
                                .status == "Active Development" or
				.status == "Pre-release Freeze")) | .name')}
for codename in $RELEASES; do
    for proposed in '' true; do
        for ppa in '' ppa:ubuntu-security-proposed/ppa; do
            if [ -n "$proposed" ] && [ -n "$ppa" ]; then
                continue
            fi
	    cat << EOSYSTEM >> systems.$$
      - ubuntu-${codename}${proposed:+-proposed}${ppa:+-ppa}:
          image: ubuntu-daily:${codename}
          environment:
            ENABLE_PROPOSED: ${proposed}
            PPA: ${ppa}
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
