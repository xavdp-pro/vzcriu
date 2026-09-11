#ifndef __CR_MOUNT_V2_H__
#define __CR_MOUNT_V2_H__

#include <sys/types.h>

#include "common/list.h"
#include "mount.h"


#include <sys/syscall.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>

/* Use the upstream Linux mount-group operation on modern kernels. */
static inline int lab_set_mount_group(const char *source, const char *target)
{
 int a = open(source, O_PATH | O_CLOEXEC), b, ret, saved;
 if (a < 0) return -1;
 b = open(target, O_PATH | O_CLOEXEC);
 if (b < 0) { saved = errno; close(a); errno = saved; return -1; }
 ret = syscall(SYS_move_mount, a, "", b, "", 0x100 | 0x4 | 0x40);
 saved = errno; close(a); close(b); errno = saved;
 return ret;
}


struct sharing_group {
	/* This pair identifies the group */
	int                     shared_id;
	int                     master_id;

	/* List of shared groups */
	struct list_head        list;

	/* List of mounts in this group */
	struct list_head        mnt_list;

	/*
	 * List of dependant shared groups:
	 * - all siblings have equal master_id
	 * - the parent has shared_id equal to children's master_id
	 *
	 * This is a bit tricky: parent pointer indicates if there is one
	 * parent sharing_group in list or only siblings.
	 * So for traversal if parent pointer is set we can do:
	 *   list_for_each_entry(t, &sg->parent->children, siblings)
	 * and overvise we can do:
	 *   list_for_each_entry(t, &sg->siblings, siblings)
	 */
	struct list_head        children;
	struct list_head        siblings;
	struct sharing_group    *parent;

	char			*source;
};

extern struct list_head nested_pidns_procs;

extern int prepare_mnt_ns_v2(void);
extern int read_mnt_ns_img_v2(struct mount_info *info);
extern int fini_restore_mntns_v2(void);
extern int cleanup_internal_yards(void);

#endif /* __CR_MOUNT_V2_H__ */
