#!/bin/bash

PLAN=${PLAN:-c4-m8}
STORAGE=${STORAGE:-30G}
RELEASES=${RELEASES:-$(curl -s https://api.launchpad.net/devel/ubuntu/series | \
        jq -r '.entries[] | select(.version >= "22.04" and
                               (.status == "Supported" or
                                .status == "Current Stable Release" or
                                .status == "Active Development" or
                                .status == "Pre-release Freeze")) | .name')}
PROPOSED=${APT_POCKETS:-"false true"}
PPA=${APT_PPAS:-"false ppa:ubuntu-security-proposed/ppa"}
for codename in $RELEASES; do
    for proposed in $PROPOSED; do
        for ppa in $PPA; do
            if [ "$proposed" != false ] && [ "$ppa" != false ]; then
                continue
            fi

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
