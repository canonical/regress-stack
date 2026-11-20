/*
 * Copyright 2026 - Canonical Ltd
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * Workaround for Ganesha SIGSEGV during CephFS export creation on Resolute.
 * Ubuntu's snapshot-upgrade-fix.patch uses dout() in MDSMap::decode(),
 * where a library client can have a null g_ceph_context. Initialize that
 * context before Ganesha loads any CephFS exports.
 *
 * https://bugs.launchpad.net/ubuntu/+source/ceph/+bug/2157583
 *
 * The Spread task enables this preload only for the affected libcephfs2
 * versions. It relies on Ceph's internal global-context symbol, so it
 * should be retired when corrected packages are available.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>

struct ceph_mount_info;

__attribute__((constructor))
static void initialise_ceph_context(void)
{
    void *library = dlopen("libcephfs.so.2", RTLD_NOW | RTLD_GLOBAL);
    if (library == NULL) {
        fprintf(stderr, "Ceph context workaround: %s\n", dlerror());
        _Exit(EXIT_FAILURE);
    }
    void **global_context = dlsym(library, "_ZN4ceph6global14g_ceph_contextE");
    int (*create_mount)(struct ceph_mount_info **, const char *) =
        dlsym(library, "ceph_create");
    void *(*get_context)(struct ceph_mount_info *) =
        dlsym(library, "ceph_get_mount_context");
    if (global_context == NULL || create_mount == NULL || get_context == NULL) {
        fprintf(stderr, "Ceph context workaround: required symbols are unavailable\n");
        _Exit(EXIT_FAILURE);
    }
    if (*global_context != NULL) {
        return;
    }

    /* Retain this unmounted handle for the lifetime of the process. */
    struct ceph_mount_info *mount = NULL;
    if (create_mount(&mount, "regress-stack-context") != 0) {
        fprintf(stderr, "Ceph context workaround: cannot create a client context\n");
        _Exit(EXIT_FAILURE);
    }
    *global_context = get_context(mount);
}
