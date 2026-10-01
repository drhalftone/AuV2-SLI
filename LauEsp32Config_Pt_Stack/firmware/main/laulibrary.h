/*********************************************************************************
 *                                                                               *
 * Copyright (c) 2026, Dr. Daniel L. Lau                                         *
 * All rights reserved.                                                          *
 *                                                                               *
 * Redistribution and use in source and binary forms, with or without            *
 * modification, is strictly forbidden.                                          *
 *                                                                               *
 * THIS SOFTWARE IS PROVIDED BY DR. DANIEL L. LAU ''AS IS'' AND ANY              *
 * EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED     *
 * WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE        *
 * DISCLAIMED. IN NO EVENT SHALL DR. DANIEL L. LAU BE LIABLE FOR ANY             *
 * DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES    *
 * (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;  *
 * LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND   *
 * ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT    *
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS *
 * SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.                  *
 *                                                                               *
 *********************************************************************************/

#ifndef LAULIBRARY_H
#define LAULIBRARY_H

#include <stdbool.h>
#include <stddef.h>

#include "cJSON.h"
#include "esp_err.h"
#include "laubitfile.h"

// THE BITSTREAM LIBRARY ON THE MICROSD CARD (SD_BITSTREAM_LIBRARY.MD SECTION 5), IMAGES ARE
// <STEM>.BIT IN /SDCARD/BITSTREAMS, EACH WITH AN OPTIONAL <STEM>.JSON SIDECAR
#define LIBRARYMOUNTPOINT   "/sdcard"
#define LIBRARYDIRECTORY    LIBRARYMOUNTPOINT "/bitstreams"

/****************************************************************************/
/****************************************************************************/
/****************************************************************************/
typedef struct {
    char description[256];
    char gitCommit[48];
    char sha256[72];
    char sourceScript[96];
} LAUSidecar;

esp_err_t lauLibraryMount(void);
bool lauLibraryIsMounted(void);
int lauLibraryCardDetectLevel(void);

// LIBRARY NAMES ARE STEMS OF [A-Za-z0-9._-], 1 TO 64 CHARACTERS, NO LEADING DOT, AND A
// TRAILING ".BIT" IS ACCEPTED AND STRIPPED, ANYTHING ELSE INCLUDING PATH TRICKS RETURNS FALSE
bool lauLibraryNormalizeName(const char *name, char *stem, size_t capacity);
void lauLibraryPath(const char *stem, const char *extension, char *path, size_t capacity);

esp_err_t lauLibraryReadHeader(const char *stem, LAUBitFileInfo *info, long *fileSize);
bool lauLibraryReadSidecar(const char *stem, LAUSidecar *sidecar);
esp_err_t lauLibrarySha256File(const char *path, char hexString[65]);

cJSON *lauLibraryList(void);
esp_err_t lauLibraryDelete(const char *stem);
cJSON *lauLibraryCardInfo(void);

// ATOMIC INSTALL: WRITE TO A TEMPORARY PATH, THEN MOVE IT INTO PLACE OVER ANY EXISTING FILE
void lauLibraryTempPath(const char *extension, char *path, size_t capacity);
esp_err_t lauLibraryInstall(const char *tempPath, const char *stem, const char *extension);

#endif // LAULIBRARY_H
