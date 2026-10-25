#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern char **environ;

typedef int (*main_fn)(int, char **, char **);
typedef int (*start_fn)(main_fn, int, char **, void (*)(void), void (*)(void), void (*)(void), void *);

static main_fn real_main;

static char *read_key(const char *path)
{
    static const char prefix[] = "SOLOIST_API_KEY=";
    char line[1024];
    char *key = NULL;
    FILE *f = fopen(path, "r");
    if (!f)
        return NULL;
    while (fgets(line, sizeof line, f)) {
        if (strncmp(line, prefix, sizeof prefix - 1) == 0) {
            char *v = line + sizeof prefix - 1;
            v[strcspn(v, " \t\r\n")] = '\0';
            if (*v)
                key = strdup(v);
            break;
        }
    }
    memset(line, 0, sizeof line);
    fclose(f);
    return key;
}

static int key_main(int argc, char **argv, char **envp)
{
    const char *path = getenv("SOLOIST_API_KEY_FILE");
    char *key = path ? read_key(path) : NULL;
    char **args;
    int i;

    (void)envp;
    unsetenv("SOLOIST_API_KEY_FILE");
    unsetenv("LD_PRELOAD");
    if (!key) {
        if (path)
            fprintf(stderr, "soloist-key: no SOLOIST_API_KEY in %s\n", path);
        return real_main(argc, argv, environ);
    }
    args = calloc((size_t)argc + 3, sizeof *args);
    if (!args)
        return 1;
    for (i = 0; i < argc; i++)
        args[i] = argv[i];
    args[argc] = "--api-key";
    args[argc + 1] = key;
    return real_main(argc + 2, args, environ);
}

int __libc_start_main(main_fn main, int argc, char **argv, void (*init)(void), void (*fini)(void),
                      void (*rtld_fini)(void), void *stack_end)
{
    start_fn real_start = (start_fn)dlsym(RTLD_NEXT, "__libc_start_main");
    real_main = main;
    return real_start(key_main, argc, argv, init, fini, rtld_fini, stack_end);
}
