//
// AI Play layer — transport: non-blocking Unix-domain-socket server.
//
// Loopback-only by construction: the transport creates an AF_UNIX socket and
// never a TCP listener (constitution security constraint, FR-014).
// NDJSON framing: one JSON request per line; responses appended to per-client
// write queues.
//
// SPDX-License-Identifier: GPL-2.0-or-later
#include "aiplay_internal.h"

#ifndef AIPLAY_UNIT_TEST

#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#define AIPLAY_MAX_CLIENTS     4
#define AIPLAY_RX_BUF          8192
#define AIPLAY_TX_BUF          65536

typedef struct aiplayClient
{
    int      fd;
    char     rx[AIPLAY_RX_BUF];
    size_t   rxLen;      // bytes buffered; complete lines are consumed
    char     tx[AIPLAY_TX_BUF];
    size_t   txLen;
    size_t   txSent;
    bool     active;
} aiplayClient;

static aiplayClient clients[AIPLAY_MAX_CLIENTS];
static int          listenFd = -1;
static char         socketPath[512];

static void transportTrace(const char *fmt, ...)
{   // pure-core-safe diagnostics (pure core has no dbgMessagef)
    char buf[256];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);
    aiplayTraceLog(buf);
}

void aiplayTransportReset(void)
{
    memset(clients, 0, sizeof(clients));
    listenFd   = -1;
    socketPath[0] = '\0';
}

bool aiplayTransportStart(const char *path)
{
    strncpy(socketPath, path, sizeof(socketPath) - 1);
    socketPath[sizeof(socketPath) - 1] = '\0';

    listenFd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (listenFd < 0)
    {
        transportTrace("AIPLAY-TRANSPORT-FAIL socket() error=%s", strerror(errno));
        return false;
    }
    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, socketPath, sizeof(addr.sun_path) - 1);
    unlink(socketPath);  // stale socket from a crashed run is not fatal
    if (bind(listenFd, (struct sockaddr *)&addr, sizeof(addr)) < 0)
    {
        // TEMP-DEBUG (bug hunt: transport not coming up after savegame load)
        transportTrace("AIPLAY-TRANSPORT-FAIL bind(%s) error=%s", socketPath, strerror(errno));
        close(listenFd);
        listenFd = -1;
        return false;
    }
    chmod(socketPath, 0600);  // user-only: local-only by construction
    if (listen(listenFd, AIPLAY_MAX_CLIENTS) < 0)
    {
        close(listenFd);
        listenFd = -1;
        unlink(socketPath);
        return false;
    }
    int flags = fcntl(listenFd, F_GETFL, 0);
    fcntl(listenFd, F_SETFL, flags | O_NONBLOCK);
    for (int i = 0; i < AIPLAY_MAX_CLIENTS; i++)
    {
        clients[i].fd = -1;
    }
    transportTrace("AIPLAY-TRANSPORT-OK listening on %s", socketPath);
    return true;
}

void aiplayTransportStop(void)
{
    for (int i = 0; i < AIPLAY_MAX_CLIENTS; i++)
    {
        if (clients[i].fd >= 0)
        {
            close(clients[i].fd);
        }
    }
    if (listenFd >= 0)
    {
        close(listenFd);
        unlink(socketPath);
        listenFd = -1;
    }
    aiplayTransportReset();
}

static void clientClose(int i)
{
    close(clients[i].fd);
    clients[i].fd      = -1;
    clients[i].active  = false;
    clients[i].rxLen   = 0;
    clients[i].txLen   = clients[i].txSent = 0;
}

static void flushTx(int i)
{
    while (clients[i].txSent < clients[i].txLen)
    {
        ssize_t n = write(
            clients[i].fd, clients[i].tx + clients[i].txSent, clients[i].txLen - clients[i].txSent);
        if (n > 0)
        {
            clients[i].txSent += (size_t)n;
            continue;
        }
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
        {
            return;  // retry next frame
        }
        clientClose(i);
        return;
    }
    if (clients[i].txSent == clients[i].txLen)
    {
        clients[i].txLen = clients[i].txSent = 0;
    }
}

static void clientLine(int i, const char *line, size_t len)
{
    aiplayJsonBuf buf;
    char          staticBuf[8192];
    aiplayJsonBufInit(&buf, staticBuf, sizeof(staticBuf));
    aiplayTransportDeliver(line, &buf);
    if (buf.oom || buf.len + clients[i].txLen + 1 >= AIPLAY_TX_BUF)
    {
        // too much to buffer: truncate politely
        aiplayJsonBufFree(&buf);
        return;
    }
    memcpy(clients[i].tx + clients[i].txLen, buf.data, buf.len);
    clients[i].txLen += buf.len;
    aiplayJsonBufFree(&buf);
    flushTx(i);
}

static void clientRx(int i)
{
    while (true)
    {
        if (clients[i].rxLen >= sizeof(clients[i].rx) - 1)
        {
            clients[i].rxLen = 0;  // oversized line: drop
        }
        ssize_t n = read(
            clients[i].fd, clients[i].rx + clients[i].rxLen,
            sizeof(clients[i].rx) - 1 - clients[i].rxLen);
        if (n > 0)
        {
            clients[i].rxLen += (size_t)n;
            clients[i].rx[clients[i].rxLen] = '\0';
            // consume complete lines
            char *start = clients[i].rx;
            char *nl;
            while ((nl = strchr(start, '\n')) != NULL)
            {
                *nl = '\0';
                clientLine(i, start, (size_t)(nl - start));
                start = nl + 1;
            }
            if (start != clients[i].rx)
            {
                size_t left = clients[i].rxLen - (size_t)(start - clients[i].rx);
                memmove(clients[i].rx, start, left + 1);
                clients[i].rxLen = left;
            }
            continue;
        }
        if (n == 0)
        {
            clientClose(i);  // peer hung up (FR-010: sim keeps running)
            return;
        }
        if (errno == EAGAIN || errno == EWOULDBLOCK)
        {
            return;
        }
        clientClose(i);
        return;
    }
}

void aiplayFramePoll(void)
{
    if (listenFd < 0)
    {
        return;
    }
    // accept new connections
    while (true)
    {
        int fd = accept(listenFd, NULL, NULL);
        if (fd < 0)
        {
            break;
        }
        int flags = fcntl(fd, F_GETFL, 0);
        fcntl(fd, F_SETFL, flags | O_NONBLOCK);
        int slot = -1;
        for (int i = 0; i < AIPLAY_MAX_CLIENTS; i++)
        {
            if (!clients[i].active)
            {
                slot = i;
                break;
            }
        }
        if (slot < 0)
        {
            close(fd);  // too many spectators-side clients; first-come-first-served
            continue;
        }
        clients[slot].fd     = fd;
        clients[slot].active = true;
        clients[slot].rxLen  = 0;
        clients[slot].txLen = clients[slot].txSent = 0;
    }
    for (int i = 0; i < AIPLAY_MAX_CLIENTS; i++)
    {
        if (clients[i].active)
        {
            clientRx(i);
            flushTx(i);
        }
    }
}

#else // AIPLAY_UNIT_TEST ------------------------------------------------------

void aiplayTransportReset(void) {}
bool aiplayTransportStart(const char *path)
{
    (void)path;
    return true;
}
void aiplayTransportStop(void) {}
void aiplayFramePoll(void) {}

#endif // AIPLAY_UNIT_TEST