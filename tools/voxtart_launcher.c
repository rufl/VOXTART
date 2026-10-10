/* SPDX-FileCopyrightText: 2026 VOXTART contributors
 *
 * SPDX-License-Identifier: MIT
 */

#include <windows.h>
#include <shellapi.h>
#include <stdbool.h>
#include <stddef.h>
#include <wchar.h>

#define PATH_CAPACITY 32768
#define COMMAND_CAPACITY 32768
#define WAIT_FOR_TITLE_MS 15000

struct window_search {
  DWORD process_id;
  HWND handle;
};

static bool append_char(wchar_t *buffer, size_t capacity, size_t *length, wchar_t value)
{
  if (*length + 1 >= capacity) {
    return false;
  }
  buffer[(*length)++] = value;
  buffer[*length] = L'\0';
  return true;
}

static bool append_text(wchar_t *buffer, size_t capacity, size_t *length, const wchar_t *text)
{
  while (*text != L'\0') {
    if (!append_char(buffer, capacity, length, *text++)) {
      return false;
    }
  }
  return true;
}

static bool append_quoted(wchar_t *buffer, size_t capacity, size_t *length, const wchar_t *text)
{
  size_t backslashes = 0;
  if (!append_char(buffer, capacity, length, L'"')) {
    return false;
  }
  for (const wchar_t *cursor = text;; cursor++) {
    const wchar_t value = *cursor;
    if (value == L'\\') {
      backslashes++;
      continue;
    }
    if (value == L'"') {
      for (size_t index = 0; index < backslashes * 2 + 1; index++) {
        if (!append_char(buffer, capacity, length, L'\\')) {
          return false;
        }
      }
      if (!append_char(buffer, capacity, length, L'"')) {
        return false;
      }
      backslashes = 0;
      continue;
    }
    for (size_t index = 0; index < backslashes; index++) {
      if (!append_char(buffer, capacity, length, L'\\')) {
        return false;
      }
    }
    backslashes = 0;
    if (value == L'\0') {
      break;
    }
    if (!append_char(buffer, capacity, length, value)) {
      return false;
    }
  }
  for (size_t index = 0; index < backslashes * 2; index++) {
    if (!append_char(buffer, capacity, length, L'\\')) {
      return false;
    }
  }
  return append_char(buffer, capacity, length, L'"');
}

static bool path_join(wchar_t *result, size_t capacity, const wchar_t *root, const wchar_t *suffix)
{
  const int written = _snwprintf_s(result, capacity, _TRUNCATE, L"%ls%ls", root, suffix);
  return written >= 0;
}
static bool parent_path(wchar_t *path)
{
  wchar_t *separator = wcsrchr(path, L'\\');
  if (separator == NULL || separator == path) {
    return false;
  }
  *separator = L'\0';
  return true;
}

static bool file_exists(const wchar_t *path)
{
  const DWORD attributes = GetFileAttributesW(path);
  return attributes != INVALID_FILE_ATTRIBUTES && !(attributes & FILE_ATTRIBUTE_DIRECTORY);
}


static bool environment_path(wchar_t *result, size_t capacity, const wchar_t *name)
{
  const DWORD length = GetEnvironmentVariableW(name, result, (DWORD)capacity);
  return length > 0 && length < capacity;
}
static bool run_hidden_and_wait(wchar_t *command_line, DWORD *exit_code)
{
  STARTUPINFOW startup = {0};
  PROCESS_INFORMATION process = {0};
  startup.cb = sizeof(startup);
  startup.dwFlags = STARTF_USESHOWWINDOW;
  startup.wShowWindow = SW_HIDE;
  const BOOL created = CreateProcessW(
      NULL, command_line, NULL, NULL, FALSE, CREATE_UNICODE_ENVIRONMENT, NULL, NULL, &startup, &process);
  if (!created) {
    return false;
  }
  WaitForSingleObject(process.hProcess, INFINITE);
  const BOOL got_code = GetExitCodeProcess(process.hProcess, exit_code);
  CloseHandle(process.hThread);
  CloseHandle(process.hProcess);
  return got_code == TRUE;
}

static BOOL CALLBACK find_process_window(HWND handle, LPARAM parameter)
{
  struct window_search *search = (struct window_search *)parameter;
  DWORD process_id = 0;
  GetWindowThreadProcessId(handle, &process_id);
  if (process_id != search->process_id || GetWindow(handle, GW_OWNER) != NULL ||
      GetWindowTextLengthW(handle) == 0) {
    return TRUE;
  }
  if (search->handle == NULL) {
    search->handle = handle;
  }
  wchar_t title[256] = {0};
  GetWindowTextW(handle, title, sizeof(title) / sizeof(title[0]));
  if (wcscmp(title, L"VoxTart") == 0) {
    search->handle = handle;
    return FALSE;
  }
  return TRUE;
}

static HWND process_window(DWORD process_id)
{
  struct window_search search = {.process_id = process_id, .handle = NULL};
  EnumWindows(find_process_window, (LPARAM)&search);
  return search.handle;
}

static BOOL CALLBACK hide_process_window(HWND handle, LPARAM parameter)
{
  const DWORD process_id = (DWORD)parameter;
  DWORD owner_process_id = 0;
  GetWindowThreadProcessId(handle, &owner_process_id);
  if (owner_process_id == process_id && GetWindow(handle, GW_OWNER) == NULL) {
    ShowWindow(handle, SW_HIDE);
  }
  return TRUE;
}

static void hide_windows(DWORD process_id)
{
  EnumWindows(hide_process_window, (LPARAM)process_id);
}
static BOOL CALLBACK set_process_title(HWND handle, LPARAM parameter)
{
  const DWORD process_id = (DWORD)parameter;
  DWORD owner_process_id = 0;
  GetWindowThreadProcessId(handle, &owner_process_id);
  if (owner_process_id == process_id && GetWindow(handle, GW_OWNER) == NULL) {
    SetWindowTextW(handle, L"VoxTart");
  }
  return TRUE;
}

static void set_process_titles(DWORD process_id)
{
  EnumWindows(set_process_title, (LPARAM)process_id);
}
static BOOL CALLBACK normalize_voxtart_window(HWND handle, LPARAM parameter)
{
  (void)parameter;
  if (GetWindow(handle, GW_OWNER) != NULL || GetWindowTextLengthW(handle) == 0) {
    return TRUE;
  }
  wchar_t title[512] = {0};
  GetWindowTextW(handle, title, sizeof(title) / sizeof(title[0]));
  if (wcsstr(title, L"VoxTart") != NULL && wcscmp(title, L"VoxTart") != 0) {
    SetWindowTextW(handle, L"VoxTart");
  }
  return TRUE;
}

static void normalize_voxtart_titles(void)
{
  EnumWindows(normalize_voxtart_window, 0);
}

static bool title_is_ready(HWND handle)
{
  wchar_t title[256] = {0};
  GetWindowTextW(handle, title, sizeof(title) / sizeof(title[0]));
  return wcscmp(title, L"VoxTart") == 0;
}

static bool reveal_when_branded(PROCESS_INFORMATION *process)
{
  const DWORD started = GetTickCount();
  while (GetTickCount() - started < WAIT_FOR_TITLE_MS) {
    normalize_voxtart_titles();
    if (WaitForSingleObject(process->hProcess, 0) == WAIT_OBJECT_0) {
      return false;
    }
    hide_windows(process->dwProcessId);
    HWND handle = process_window(process->dwProcessId);
    if (handle != NULL && title_is_ready(handle)) {
      ShowWindow(handle, SW_MAXIMIZE);
      ShowWindow(handle, SW_SHOW);
      SetForegroundWindow(handle);
      return true;
    }
    Sleep(1);
  }
  hide_windows(process->dwProcessId);
  normalize_voxtart_titles();
  set_process_titles(process->dwProcessId);
  HWND handle = process_window(process->dwProcessId);
  if (handle != NULL) {
    ShowWindow(handle, SW_MAXIMIZE);
    ShowWindow(handle, SW_SHOW);
    SetForegroundWindow(handle);
  }
  return false;
}

static bool argument_requests_passthrough(int argc, wchar_t **argv)
{
  for (int index = 1; index < argc; index++) {
    if (wcscmp(argv[index], L"--background") == 0 || wcscmp(argv[index], L"-b") == 0 ||
        wcscmp(argv[index], L"--python") == 0 || wcscmp(argv[index], L"-P") == 0 ||
        wcscmp(argv[index], L"--python-expr") == 0 || wcscmp(argv[index], L"--python-text") == 0 ||
        wcscmp(argv[index], L"--help") == 0 || wcscmp(argv[index], L"-h") == 0 ||
        wcscmp(argv[index], L"--version") == 0 || wcscmp(argv[index], L"-v") == 0) {
      return true;
    }
  }
  return false;
}

static bool build_command(wchar_t *command,
                          size_t capacity,
                          const wchar_t *blender,
                          const wchar_t *startup,
                          int argc,
                          wchar_t **argv,
                          bool inject_startup)
{
  size_t length = 0;
  command[0] = L'\0';
  if (!append_quoted(command, capacity, &length, blender)) {
    return false;
  }
  if (inject_startup &&
      (!append_text(command, capacity, &length, L" --python ") ||
       !append_quoted(command, capacity, &length, startup))) {
    return false;
  }
  for (int index = 1; index < argc; index++) {
    if (!append_char(command, capacity, &length, L' ') ||
        !append_quoted(command, capacity, &length, argv[index])) {
      return false;
    }
  }
  return true;
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR command_line, int show_command)
{
  (void)instance;
  (void)previous;
  (void)command_line;
  (void)show_command;

  wchar_t executable_directory[PATH_CAPACITY] = {0};
  const DWORD path_length = GetModuleFileNameW(NULL, executable_directory, PATH_CAPACITY);
  if (path_length == 0 || path_length >= PATH_CAPACITY || !parent_path(executable_directory)) {
    return 2;
  }

  wchar_t root_path[PATH_CAPACITY];
  wchar_t blender_path[PATH_CAPACITY];
  wchar_t startup_path[PATH_CAPACITY];
  wchar_t preferences_init_path[PATH_CAPACITY];
  wchar_t preferences_directory[PATH_CAPACITY];
  wchar_t preferences_path[PATH_CAPACITY];
#if defined(VOXTART_RUNTIME_WRAPPER)
  if (!path_join(root_path, PATH_CAPACITY, executable_directory, L"") ||
      !parent_path(root_path) || !parent_path(root_path) || !parent_path(root_path) ||
      !path_join(blender_path, PATH_CAPACITY, executable_directory, L"\\blender-core.exe")) {
    return 3;
  }
#else
  if (!path_join(root_path, PATH_CAPACITY, executable_directory, L"") ||
      !path_join(blender_path,
                 PATH_CAPACITY,
                 root_path,
                 L"\\build\\blender-5.2.2\\blender-5.2.2-windows-x64\\blender.exe")) {
    return 3;
  }
  wchar_t core_path[PATH_CAPACITY];
  if (!path_join(core_path,
                 PATH_CAPACITY,
                 root_path,
                 L"\\build\\blender-5.2.2\\blender-5.2.2-windows-x64\\blender-core.exe")) {
    return 3;
  }
  if (file_exists(core_path) && wcscpy_s(blender_path, PATH_CAPACITY, core_path) != 0) {
    return 3;
  }
#endif
  if (!path_join(startup_path, PATH_CAPACITY, root_path, L"\\tools\\voxtart_desktop_start.py") ||
      !path_join(preferences_init_path,
                 PATH_CAPACITY,
                 root_path,
                 L"\\tools\\voxtart_preferences_init.py") ||
      !file_exists(blender_path) || !file_exists(startup_path)) {
    return 4;
  }
  if (!environment_path(preferences_directory, PATH_CAPACITY, L"VOXTART_CONFIG") &&
      !path_join(preferences_directory, PATH_CAPACITY, root_path, L"\\config\\blender")) {
    return 4;
  }
  if (!path_join(preferences_path, PATH_CAPACITY, preferences_directory, L"\\userpref.blend")) {
    return 4;
  }

  SetCurrentDirectoryW(root_path);
  SetEnvironmentVariableW(L"BLENDER_USER_CONFIG", preferences_directory);
  CreateDirectoryW(L"config", NULL);
  CreateDirectoryW(preferences_directory, NULL);

  if (!file_exists(preferences_path)) {
    wchar_t preference_command[COMMAND_CAPACITY];
    size_t length = 0;
    preference_command[0] = L'\0';
    if (!append_quoted(preference_command, COMMAND_CAPACITY, &length, blender_path) ||
        !append_text(preference_command, COMMAND_CAPACITY, &length, L" --background --python ") ||
        !append_quoted(preference_command, COMMAND_CAPACITY, &length, preferences_init_path)) {
      return 5;
    }
    DWORD exit_code = 1;
    if (!run_hidden_and_wait(preference_command, &exit_code) || exit_code != 0) {
      return 6;
    }
  }

  int argc = 0;
  wchar_t **argv = CommandLineToArgvW(GetCommandLineW(), &argc);
  if (argv == NULL) {
    return 7;
  }
#if defined(VOXTART_RUNTIME_WRAPPER)
  const bool inject_startup = !argument_requests_passthrough(argc, argv);
#else
  const bool inject_startup = true;
#endif
  wchar_t command[COMMAND_CAPACITY];
  const bool command_ok = build_command(
      command, COMMAND_CAPACITY, blender_path, startup_path, argc, argv, inject_startup);
  LocalFree(argv);
  if (!command_ok) {
    return 8;
  }

  STARTUPINFOW startup = {0};
  PROCESS_INFORMATION process = {0};
  startup.cb = sizeof(startup);
  startup.dwFlags = STARTF_USESHOWWINDOW;
  startup.wShowWindow = SW_HIDE;
  if (!CreateProcessW(
          NULL, command, NULL, NULL, FALSE, CREATE_UNICODE_ENVIRONMENT, NULL, root_path, &startup, &process)) {
    return 9;
  }

  hide_windows(process.dwProcessId);
  reveal_when_branded(&process);
  while (WaitForSingleObject(process.hProcess, 100) == WAIT_TIMEOUT) {
    normalize_voxtart_titles();
    set_process_titles(process.dwProcessId);
  }
  DWORD exit_code = 1;
  GetExitCodeProcess(process.hProcess, &exit_code);
  CloseHandle(process.hThread);
  CloseHandle(process.hProcess);
  return (int)exit_code;
}
