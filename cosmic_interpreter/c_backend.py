from __future__ import annotations

import ast
import codecs
import math
import re
from dataclasses import dataclass
from pathlib import Path

from .expression_syntax import transform_braced_expression
from .numeric_syntax import normalize_rag_calls
from .parser import ParseError, Program, Statement


_DLL_C_TYPES: dict[str, str] = {
    "VOID": "void",
    "INT": "int",
    "UINT": "unsigned int",
    "LONG": "long",
    "DWORD": "unsigned long",
    "WORD": "unsigned short",
    "BYTE": "unsigned char",
    "CHAR": "char",
    "PTR": "void *",
    "HWND": "void *",
    "STR": "const char *",
    "LPCSTR": "const char *",
    "FLOAT": "float",
    "DOUBLE": "double",
}

_DLL_NUMERIC_RETURNS: dict[str, str] = {
    "INT": "long long",
    "UINT": "unsigned long long",
    "LONG": "long long",
    "DWORD": "unsigned long long",
    "WORD": "unsigned long long",
    "BYTE": "unsigned long long",
    "CHAR": "long long",
    "FLOAT": "double",
    "DOUBLE": "double",
    "PTR": "unsigned long long",
    "HWND": "unsigned long long",
}

_DLL_FORMATS: dict[str, str] = {
    "INT": "%lld",
    "UINT": "%llu",
    "LONG": "%lld",
    "DWORD": "%llu",
    "WORD": "%llu",
    "BYTE": "%llu",
    "CHAR": "%lld",
    "FLOAT": "%.17g",
    "DOUBLE": "%.17g",
    "PTR": "%llu",
    "HWND": "%llu",
}

_DLL_TEXT_PARAMS = {"STR", "LPCSTR"}
_DLL_POINTER_PARAMS = {"PTR", "HWND", "VOID"}


def _c_cast_dll_argument(rendered: str, type_name: str | None) -> str:
    """按 #extern 声明的类型把参数转成 C 侧类型。

    _c_expression 会把数字一律生成为 (double)(...)，直接传给
    void* / unsigned long 形参会导致 GCC 报 incompatible type。
    """
    if not type_name:
        return rendered
    ctype = _DLL_C_TYPES.get(type_name)
    if ctype is None:
        return rendered
    is_text_literal = rendered.lstrip().startswith('"')
    if type_name in _DLL_TEXT_PARAMS:
        if is_text_literal:
            return f"(({ctype})({rendered}))"
        # 数值 0 当作空指针，其余经由整数转换避免 double -> pointer
        return f"(({ctype})(unsigned long long)({rendered}))"
    if type_name in _DLL_POINTER_PARAMS:
        return f"(({ctype})(unsigned long long)({rendered}))"
    if type_name in {"FLOAT", "DOUBLE"}:
        return f"(({ctype})({rendered}))"
    return f"(({ctype})({rendered}))"

_BUILTIN_FUNCTION_NAMES = {
    "abs", "sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
    "rag_range", "sinh", "cosh", "tanh", "exp", "log", "log10", "log2",
    "floor", "ceil", "pow", "fmod", "hypot", "erf", "gamma", "chr", "ord",
    "strlen", "char_at", "read_text", "write_text", "real", "imag", "conj",
    "cabs", "array_sum", "array_at", "dot", "matrix2_det", "matrix2_mul_entry",
    "blas_dot", "lapack_determinant2", "array_len", "array_set", "array_push",
    "mean", "std", "min", "max", "err_count",
    "hex", "unhex", "bytes_len", "bytes_get", "bytes_set",
    "matrix_rows", "matrix_cols", "matrix_at", "matrix_mul", "matrix_transpose",
    "matrix2_inverse", "matrix3_inverse", "solve_linear",
    "median", "variance", "correlation", "percentile", "trapz", "simpson",
}


_C_RUNTIME = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <complex.h>
#include <stdint.h>
#include <time.h>
#include <limits.h>
#include <errno.h>
#ifdef _WIN32
#include <windows.h>
#include <process.h>
#define popen _popen
#define pclose _pclose
#define sleep_ms(ms) Sleep(ms)
#else
#include <unistd.h>
#include <strings.h>
#include <dlfcn.h>
#define _stricmp strcasecmp
#define sleep_ms(ms) usleep((useconds_t)(ms) * 1000)
#endif

enum { STATUS_ERROR = -1, STATUS_OK = 0, STATUS_STOP = 1 };
typedef struct {
    unsigned char *bytes;
    const char *encoding;
    void *array_data;
    size_t array_length;
    int array_value;
    int array_complex;
    size_t matrix_rows;
    size_t matrix_columns;
    int initialized;
    int killed;
} Variable;
typedef struct { const double *data; size_t length; } CosmicArray;
typedef struct { const double complex *data; size_t length; } CosmicComplexArray;
typedef struct { double *data; size_t rows; size_t columns; } CosmicMatrix;
typedef struct {
    double *data;
    double complex *complex_data;
    size_t length;
    int initialized;
    int complex_mode;
    size_t matrix_rows;
    size_t matrix_columns;
} ArrayVariable;
typedef struct { Variable vars[VAR_COUNT]; ArrayVariable arrays[ARRAY_COUNT]; } Context;
typedef struct { int status; char *value; } Result;
typedef struct TapeCell { int symbol; struct TapeCell *left; struct TapeCell *right; } TapeCell;
typedef struct { TapeCell *current; int initialized; } Tape;
typedef struct ArrayAllocation { void *data; struct ArrayAllocation *next; } ArrayAllocation;
static ArrayAllocation *cosmic_array_allocations;
static int block_status[BLOCK_COUNT];
static int latest_block_status = STATUS_ERROR;
static int cosmic_error_count = 0;
static Tape tapes[TAPE_COUNT];
static char *get_variable(Context *context, int index, int line);

static void fail_at(int line, const char *message) {
    fprintf(stderr, "line %d: %s\n", line, message);
    exit(1);
}

static char *copy_text(const char *text) {
    if (!text) return NULL;
    size_t size = strlen(text) + 1;
    char *copy = (char *)malloc(size);
    if (!copy) fail_at(0, "out of memory");
    memcpy(copy, text, size);
    return copy;
}

static unsigned char *copy_bytes(const unsigned char *bytes) {
    if (!bytes) return NULL;
    size_t size = strlen((const char *)bytes) + 1;
    unsigned char *copy = (unsigned char *)malloc(size);
    if (!copy) fail_at(0, "out of memory");
    memcpy(copy, bytes, size);
    return copy;
}

static void set_result(Result *result, int status, const char *value) {
    char *copy = copy_text(value);
    free(result->value);
    result->status = status;
    result->value = copy;
}

static void copy_result(Result *destination, const Result *source) {
    set_result(destination, source->status, source->value);
}

static int variable_matches(Context *context, int index, const char *expected, int line) {
    Variable *variable = &context->vars[index];
    if (variable->killed) return _stricmp("killer", expected) == 0;
    char *value = get_variable(context, index, line);
    if (!value) return 0;
    int matches = _stricmp(value, expected) == 0;
    free(value);
    return matches;
}

static void free_context(Context *context) {
    for (int i = 0; i < VAR_COUNT; ++i) {
        free(context->vars[i].bytes);
        free(context->vars[i].array_data);
        context->vars[i].bytes = NULL;
        context->vars[i].array_data = NULL;
    }
    for (int i = 0; i < ARRAY_COUNT; ++i) {
        free(context->arrays[i].data);
        free(context->arrays[i].complex_data);
        context->arrays[i].data = NULL;
        context->arrays[i].complex_data = NULL;
        context->arrays[i].length = 0;
        context->arrays[i].initialized = 0;
    }
}

static void inherit_context(Context *destination, const Context *source) {
    for (int i = 0; i < VAR_COUNT; ++i) {
        destination->vars[i] = source->vars[i];
        destination->vars[i].bytes = copy_bytes(source->vars[i].bytes);
        destination->vars[i].array_data = NULL;
        if (source->vars[i].array_data && source->vars[i].array_length) {
            size_t item_size = source->vars[i].array_complex
                ? sizeof(double complex) : sizeof(double);
            destination->vars[i].array_data = malloc(source->vars[i].array_length * item_size);
            if (!destination->vars[i].array_data) fail_at(0, "out of memory");
            memcpy(
                destination->vars[i].array_data,
                source->vars[i].array_data,
                source->vars[i].array_length * item_size
            );
        }
    }
    for (int i = 0; i < ARRAY_COUNT; ++i) {
        destination->arrays[i] = source->arrays[i];
        destination->arrays[i].data = NULL;
        destination->arrays[i].complex_data = NULL;
        if (source->arrays[i].initialized && source->arrays[i].length) {
            size_t item_size = source->arrays[i].complex_mode
                ? sizeof(double complex) : sizeof(double);
            void *data = malloc(source->arrays[i].length * item_size);
            if (!data) fail_at(0, "out of memory");
            memcpy(
                data,
                source->arrays[i].complex_mode
                    ? (const void *)source->arrays[i].complex_data
                    : (const void *)source->arrays[i].data,
                source->arrays[i].length * item_size
            );
            if (source->arrays[i].complex_mode) destination->arrays[i].complex_data = data;
            else destination->arrays[i].data = data;
        }
    }
}

#ifdef _WIN32
static unsigned int code_page(const char *encoding) {
    if (!encoding || _stricmp(encoding, "UTF-8") == 0 || _stricmp(encoding, "UTF8") == 0) return 65001;
    if (_stricmp(encoding, "GBK") == 0 || _stricmp(encoding, "CP936") == 0 || _stricmp(encoding, "GB2312") == 0) return 936;
    if (_stricmp(encoding, "ASCII") == 0 || _stricmp(encoding, "US-ASCII") == 0) return 20127;
    if (_stricmp(encoding, "CP1252") == 0 || _stricmp(encoding, "WINDOWS-1252") == 0) return 1252;
    return GetACP();
}

static unsigned char *convert_text(const char *text, unsigned int from_page, unsigned int to_page) {
    if (!text) return NULL;
    int wide_count = MultiByteToWideChar(from_page, 0, text, -1, NULL, 0);
    if (!wide_count) return (unsigned char *)copy_text(text);
    wchar_t *wide = (wchar_t *)malloc((size_t)wide_count * sizeof(wchar_t));
    if (!wide) fail_at(0, "out of memory");
    MultiByteToWideChar(from_page, 0, text, -1, wide, wide_count);
    int byte_count = WideCharToMultiByte(to_page, 0, wide, -1, NULL, 0, NULL, NULL);
    if (!byte_count) {
        free(wide);
        fail_at(0, "text conversion failed");
    }
    unsigned char *bytes = (unsigned char *)malloc((size_t)byte_count);
    if (!bytes) fail_at(0, "out of memory");
    WideCharToMultiByte(to_page, 0, wide, -1, (char *)bytes, byte_count, NULL, NULL);
    free(wide);
    return bytes;
}
#endif

static unsigned char *encode_value(const char *text, const char *encoding) {
#ifdef _WIN32
    return convert_text(text, 65001, code_page(encoding));
#else
    (void)encoding;
    return (unsigned char *)copy_text(text);
#endif
}

static char *decode_value(const unsigned char *bytes, const char *encoding) {
    if (!bytes) return NULL;
#ifdef _WIN32
    return (char *)convert_text((const char *)bytes, code_page(encoding), 65001);
#else
    (void)encoding;
    return copy_text((const char *)bytes);
#endif
}

static void set_variable(Context *context, int index, const char *value, const char *encoding, int line) {
    if (!value) fail_at(line, "up has no value on the previous line");
    const char *effective_encoding = encoding ? encoding : "UTF-8";
    unsigned char *encoded = encode_value(value, effective_encoding);
    free(context->vars[index].bytes);
    free(context->vars[index].array_data);
    context->vars[index].array_data = NULL;
    context->vars[index].array_length = 0;
    context->vars[index].array_value = 0;
    context->vars[index].array_complex = 0;
    context->vars[index].matrix_rows = 0;
    context->vars[index].matrix_columns = 0;
    context->vars[index].bytes = encoded;
    context->vars[index].encoding = effective_encoding;
    context->vars[index].initialized = 1;
    context->vars[index].killed = 0;
}

static void set_array_variable(Context *context, int index, CosmicArray source, int line) {
    if (source.length > 1000000) fail_at(line, "arrays may contain at most one million items");
    double *copy = NULL;
    if (source.length) {
        copy = (double *)malloc(source.length * sizeof(double));
        if (!copy) fail_at(line, "out of memory");
        memcpy(copy, source.data, source.length * sizeof(double));
    }
    Variable *variable = &context->vars[index];
    free(variable->bytes);
    free(variable->array_data);
    variable->bytes = NULL;
    variable->array_data = copy;
    variable->array_length = source.length;
    variable->array_value = 1;
    variable->array_complex = 0;
    variable->matrix_rows = 0;
    variable->matrix_columns = 0;
    variable->initialized = 1;
    variable->killed = 0;
}

static void set_complex_array_variable(
    Context *context, int index, CosmicComplexArray source, int line
) {
    if (source.length > 1000000) fail_at(line, "arrays may contain at most one million items");
    double complex *copy = NULL;
    if (source.length) {
        copy = (double complex *)malloc(source.length * sizeof(double complex));
        if (!copy) fail_at(line, "out of memory");
        memcpy(copy, source.data, source.length * sizeof(double complex));
    }
    Variable *variable = &context->vars[index];
    free(variable->bytes);
    free(variable->array_data);
    variable->bytes = NULL;
    variable->array_data = copy;
    variable->array_length = source.length;
    variable->array_value = 1;
    variable->array_complex = 1;
    variable->matrix_rows = 0;
    variable->matrix_columns = 0;
    variable->initialized = 1;
    variable->killed = 0;
}

static char *get_variable(Context *context, int index, int line) {
    Variable *variable = &context->vars[index];
    if (!variable->initialized) return NULL;
    if (variable->killed) return NULL;
    if (variable->array_value) {
        fail_at(line, "array variable cannot be used as text");
    }
    return decode_value(variable->bytes, variable->encoding);
}

static double numeric_variable(Context *context, int index, int line) {
    char *text = get_variable(context, index, line);
    if (!text) fail_at(line, "numeric variable is empty or killed");
    char *end = NULL;
    errno = 0;
    double value = strtod(text, &end);
    int invalid = errno || end == text || *end != '\0' || !isfinite(value);
    free(text);
    if (invalid) {
        fail_at(line, "variable does not contain a finite number");
    }
    return value;
}

static double complex complex_variable(Context *context, int index, int line) {
    char *text = get_variable(context, index, line);
    if (!text) fail_at(line, "complex variable is empty or killed");
    char *end = NULL;
    errno = 0;
    double real = strtod(text, &end);
    double imag = 0.0;
    if (errno || end == text) {
        free(text);
        fail_at(line, "variable does not contain a valid complex number");
    }
    if (*end) {
        char *imag_end = NULL;
        errno = 0;
        imag = strtod(end, &imag_end);
        if (errno || imag_end == end || strcmp(imag_end, "i") != 0) {
            free(text);
            fail_at(line, "variable does not contain a valid complex number");
        }
    }
    free(text);
    return real + imag * I;
}

static double vector_sum(const double *values, size_t count) {
    double total = 0.0;
    for (size_t i = 0; i < count; ++i) total += values[i];
    return total;
}

static double vector_dot(const double *left, const double *right, size_t count) {
    double total = 0.0;
    for (size_t i = 0; i < count; ++i) total += left[i] * right[i];
    return total;
}

static double vector_at(const double *values, size_t count, double index, int line) {
    if (!isfinite(index) || index != trunc(index) || index < 0 || index >= count) {
        fail_at(line, "array index is outside the array");
    }
    return values[(size_t)index];
}

static CosmicArray cosmic_array(const double *values, size_t count) {
    CosmicArray result = { values, count };
    return result;
}

static CosmicComplexArray cosmic_complex_array(const double complex *values, size_t count) {
    CosmicComplexArray result = { values, count };
    return result;
}

static CosmicArray cosmic_get_array(Context *context, int index, int line) {
    ArrayVariable *array = &context->arrays[index];
    if (!array->initialized) fail_at(line, "array is not initialized");
    if (array->complex_mode) fail_at(line, "complex array used in real math mode");
    return cosmic_array(array->data, array->length);
}

static CosmicComplexArray cosmic_get_complex_array(Context *context, int index, int line) {
    ArrayVariable *array = &context->arrays[index];
    if (!array->initialized) fail_at(line, "array is not initialized");
    if (!array->complex_mode) fail_at(line, "real array used in complex math mode");
    return cosmic_complex_array(array->complex_data, array->length);
}

static CosmicArray cosmic_get_variable_array(Context *context, int index, int line) {
    Variable *variable = &context->vars[index];
    if (!variable->initialized || variable->killed || !variable->array_value) {
        fail_at(line, "variable is not an array");
    }
    if (variable->array_complex) fail_at(line, "complex array used in real math mode");
    return cosmic_array((const double *)variable->array_data, variable->array_length);
}

static CosmicComplexArray cosmic_get_variable_complex_array(
    Context *context, int index, int line
) {
    Variable *variable = &context->vars[index];
    if (!variable->initialized || variable->killed || !variable->array_value) {
        fail_at(line, "variable is not an array");
    }
    if (!variable->array_complex) fail_at(line, "real array used in complex math mode");
    return cosmic_complex_array(
        (const double complex *)variable->array_data,
        variable->array_length
    );
}

static char *copy_variable_value(
    Context *context, int destination, int source, const char *encoding, int line
) {
    Variable *variable = &context->vars[source];
    if (!variable->initialized || variable->killed) {
        fail_at(line, "variable is not declared or is unavailable");
    }
    const char *effective_encoding = encoding ? encoding : variable->encoding;
    if (variable->array_value) {
        if (effective_encoding) context->vars[destination].encoding = effective_encoding;
        if (variable->array_complex) {
            set_complex_array_variable(
                context,
                destination,
                cosmic_get_variable_complex_array(context, source, line),
                line
            );
        } else {
            set_array_variable(
                context,
                destination,
                cosmic_get_variable_array(context, source, line),
                line
            );
        }
        return NULL;
    }
    char *text = get_variable(context, source, line);
    if (!text) fail_at(line, "variable is not declared or is empty");
    set_variable(context, destination, text, effective_encoding, line);
    return text;
}

static void cosmic_store_array(Context *context, int index, CosmicArray source, int line) {
    if (source.length > 1000000) fail_at(line, "arrays may contain at most one million items");
    double *data = NULL;
    if (source.length) {
        data = (double *)malloc(source.length * sizeof(double));
        if (!data) fail_at(line, "out of memory");
        memcpy(data, source.data, source.length * sizeof(double));
    }
    free(context->arrays[index].data);
    free(context->arrays[index].complex_data);
    context->arrays[index].data = data;
    context->arrays[index].complex_data = NULL;
    context->arrays[index].length = source.length;
    context->arrays[index].initialized = 1;
    context->arrays[index].complex_mode = 0;
    context->arrays[index].matrix_rows = 0;
    context->arrays[index].matrix_columns = 0;
}

static void cosmic_store_complex_array(
    Context *context, int index, CosmicComplexArray source, int line
) {
    if (source.length > 1000000) fail_at(line, "arrays may contain at most one million items");
    double complex *data = NULL;
    if (source.length) {
        data = (double complex *)malloc(source.length * sizeof(double complex));
        if (!data) fail_at(line, "out of memory");
        memcpy(data, source.data, source.length * sizeof(double complex));
    }
    free(context->arrays[index].data);
    free(context->arrays[index].complex_data);
    context->arrays[index].data = NULL;
    context->arrays[index].complex_data = data;
    context->arrays[index].length = source.length;
    context->arrays[index].initialized = 1;
    context->arrays[index].complex_mode = 1;
    context->arrays[index].matrix_rows = 0;
    context->arrays[index].matrix_columns = 0;
}

static double cosmic_array_length(CosmicArray array) {
    return (double)array.length;
}

static double cosmic_complex_array_length(CosmicComplexArray array) {
    return (double)array.length;
}

static void cosmic_release_arrays(void) {
    while (cosmic_array_allocations) {
        ArrayAllocation *allocation = cosmic_array_allocations;
        cosmic_array_allocations = allocation->next;
        free(allocation->data);
        free(allocation);
    }
}

static double *cosmic_allocate_array(size_t count, int line) {
    double *data = (double *)malloc((count ? count : 1) * sizeof(double));
    ArrayAllocation *allocation = (ArrayAllocation *)malloc(sizeof(ArrayAllocation));
    if (!data || !allocation) {
        free(data);
        free(allocation);
        fail_at(line, "out of memory");
    }

    allocation->data = data;
    allocation->next = cosmic_array_allocations;
    cosmic_array_allocations = allocation;
    return data;
}

static double complex *cosmic_allocate_complex_array(size_t count, int line) {
    double complex *data = (double complex *)malloc(
        (count ? count : 1) * sizeof(double complex)
    );
    ArrayAllocation *allocation = (ArrayAllocation *)malloc(sizeof(ArrayAllocation));
    if (!data || !allocation) {
        free(data);
        free(allocation);
        fail_at(line, "out of memory");
    }
    allocation->data = data;
    allocation->next = cosmic_array_allocations;
    cosmic_array_allocations = allocation;
    return data;
}

static size_t cosmic_array_index(CosmicArray array, double index, int line, int allow_end) {
    if (!isfinite(index) || index != trunc(index) || index < 0 ||
        index > (double)array.length || (!allow_end && index == (double)array.length)) {
        fail_at(line, "array index is outside the array");
    }
    return (size_t)index;
}

static CosmicMatrix cosmic_matrix(const double *source, size_t rows, size_t columns, int line) {
    CosmicMatrix matrix;
    matrix.rows = rows;
    matrix.columns = columns;
    matrix.data = NULL;
    size_t count = rows * columns;
    if (count) {
        matrix.data = cosmic_allocate_array(count, line);
        if (source) memcpy(matrix.data, source, count * sizeof(double));
        else memset(matrix.data, 0, count * sizeof(double));
    }
    return matrix;
}

static double cosmic_matrix_at(CosmicMatrix matrix, double row, double column, int line) {
    if (!isfinite(row) || row != trunc(row) || row < 0 || row >= (double)matrix.rows)
        fail_at(line, "matrix row is outside the matrix");
    if (!isfinite(column) || column != trunc(column) || column < 0 || column >= (double)matrix.columns)
        fail_at(line, "matrix column is outside the matrix");
    return matrix.data[(size_t)row * matrix.columns + (size_t)column];
}

static double cosmic_matrix_rows(CosmicMatrix matrix) { return (double)matrix.rows; }

static double cosmic_matrix_columns(CosmicMatrix matrix) { return (double)matrix.columns; }

static CosmicMatrix cosmic_matrix_mul(CosmicMatrix left, CosmicMatrix right, int line) {
    if (left.columns != right.rows)
        fail_at(line, "matrix_mul inner dimensions do not match");
    CosmicMatrix result = cosmic_matrix(NULL, left.rows, right.columns, line);
    for (size_t row = 0; row < left.rows; ++row) {
        for (size_t column = 0; column < right.columns; ++column) {
            double total = 0.0;
            for (size_t k = 0; k < left.columns; ++k) {
                total += left.data[row * left.columns + k] * right.data[k * right.columns + column];
            }
            result.data[row * right.columns + column] = total;
        }
    }
    return result;
}

static CosmicMatrix cosmic_matrix_transpose(CosmicMatrix matrix, int line) {
    CosmicMatrix result = cosmic_matrix(NULL, matrix.columns, matrix.rows, line);
    for (size_t row = 0; row < matrix.rows; ++row) {
        for (size_t column = 0; column < matrix.columns; ++column) {
            result.data[column * matrix.rows + row] = matrix.data[row * matrix.columns + column];
        }
    }
    return result;
}

static CosmicMatrix cosmic_matrix_inverse(CosmicMatrix matrix, int order, int line) {
    if (matrix.rows != (size_t)order || matrix.columns != (size_t)order)
        fail_at(line, "matrix inverse requires a square matrix of the requested order");
    int width = 2 * order;
    double *work = cosmic_allocate_array((size_t)width * (size_t)order, line);
    for (int row = 0; row < order; ++row) {
        for (int column = 0; column < width; ++column) {
            double value = 0.0;
            if (column < order) value = matrix.data[(size_t)row * (size_t)order + (size_t)column];
            else if (column - order == row) value = 1.0;
            work[(size_t)row * (size_t)width + (size_t)column] = value;
        }
    }
    for (int pivot = 0; pivot < order; ++pivot) {
        int best = pivot;
        for (int row = pivot + 1; row < order; ++row) {
            if (fabs(work[(size_t)row * (size_t)width + (size_t)pivot]) >
                fabs(work[(size_t)best * (size_t)width + (size_t)pivot])) {
                best = row;
            }
        }
        if (fabs(work[(size_t)best * (size_t)width + (size_t)pivot]) < 1e-12)
            fail_at(line, "matrix is singular and cannot be inverted");
        if (best != pivot) {
            for (int column = 0; column < width; ++column) {
                double swap = work[(size_t)pivot * (size_t)width + (size_t)column];
                work[(size_t)pivot * (size_t)width + (size_t)column] =
                    work[(size_t)best * (size_t)width + (size_t)column];
                work[(size_t)best * (size_t)width + (size_t)column] = swap;
            }
        }
        double scale = work[(size_t)pivot * (size_t)width + (size_t)pivot];
        for (int column = 0; column < width; ++column) {
            work[(size_t)pivot * (size_t)width + (size_t)column] /= scale;
        }
        for (int row = 0; row < order; ++row) {
            if (row == pivot) continue;
            double factor = work[(size_t)row * (size_t)width + (size_t)pivot];
            if (factor == 0.0) continue;
            for (int column = 0; column < width; ++column) {
                work[(size_t)row * (size_t)width + (size_t)column] -=
                    factor * work[(size_t)pivot * (size_t)width + (size_t)column];
            }
        }
    }
    CosmicMatrix result = cosmic_matrix(NULL, (size_t)order, (size_t)order, line);
    for (int row = 0; row < order; ++row) {
        for (int column = 0; column < order; ++column) {
            result.data[(size_t)row * (size_t)order + (size_t)column] =
                work[(size_t)row * (size_t)width + (size_t)(order + column)];
        }
    }
    return result;
}

static CosmicArray cosmic_solve_linear(CosmicMatrix matrix, CosmicArray vector, int line) {
    if (matrix.rows != matrix.columns)
        fail_at(line, "solve_linear requires a square matrix");
    if (vector.length != matrix.rows)
        fail_at(line, "solve_linear requires a right-hand side with one value per row");
    size_t order = matrix.rows;
    size_t width = order + 1;
    double *work = cosmic_allocate_array(width * order, line);
    for (size_t row = 0; row < order; ++row) {
        for (size_t column = 0; column < order; ++column) {
            work[row * width + column] = matrix.data[row * order + column];
        }
        work[row * width + order] = vector.data[row];
    }
    for (size_t pivot = 0; pivot < order; ++pivot) {
        size_t best = pivot;
        for (size_t row = pivot + 1; row < order; ++row) {
            if (fabs(work[row * width + pivot]) > fabs(work[best * width + pivot])) best = row;
        }
        if (fabs(work[best * width + pivot]) < 1e-12)
            fail_at(line, "matrix is singular and cannot be solved");
        if (best != pivot) {
            for (size_t column = 0; column < width; ++column) {
                double swap = work[pivot * width + column];
                work[pivot * width + column] = work[best * width + column];
                work[best * width + column] = swap;
            }
        }
        double scale = work[pivot * width + pivot];
        for (size_t column = 0; column < width; ++column) {
            work[pivot * width + column] /= scale;
        }
        for (size_t row = 0; row < order; ++row) {
            if (row == pivot) continue;
            double factor = work[row * width + pivot];
            if (factor == 0.0) continue;
            for (size_t column = 0; column < width; ++column) {
                work[row * width + column] -= factor * work[pivot * width + column];
            }
        }
    }
    double *solution = cosmic_allocate_array(order, line);
    for (size_t row = 0; row < order; ++row) solution[row] = work[row * width + order];
    return cosmic_array(solution, order);
}

static void cosmic_store_matrix(
    Context *context, int index, CosmicMatrix source, int line
) {
    if (source.rows == 0 || source.columns == 0)
        fail_at(line, "matrix must have at least one row and one column");
    size_t count = source.rows * source.columns;
    if (count > 1000000) fail_at(line, "arrays may contain at most one million items");
    double *data = NULL;
    if (count) {
        data = (double *)malloc(count * sizeof(double));
        if (!data) fail_at(line, "out of memory");
        memcpy(data, source.data, count * sizeof(double));
    }
    free(context->arrays[index].data);
    free(context->arrays[index].complex_data);
    context->arrays[index].data = data;
    context->arrays[index].complex_data = NULL;
    context->arrays[index].length = count;
    context->arrays[index].initialized = 1;
    context->arrays[index].complex_mode = 0;
    context->arrays[index].matrix_rows = source.rows;
    context->arrays[index].matrix_columns = source.columns;
}

static CosmicMatrix cosmic_get_matrix(Context *context, int index, int line) {
    ArrayVariable *array = &context->arrays[index];
    if (!array->initialized) fail_at(line, "matrix is not declared or is empty");
    if (array->matrix_rows == 0 || array->matrix_columns == 0)
        fail_at(line, "value is not a matrix");
    CosmicMatrix matrix = { array->data, array->matrix_rows, array->matrix_columns };
    return matrix;
}

static void set_matrix_variable(Context *context, int index, CosmicMatrix source, int line) {
    if (source.rows == 0 || source.columns == 0)
        fail_at(line, "matrix must have at least one row and one column");
    size_t count = source.rows * source.columns;
    if (count > 1000000) fail_at(line, "arrays may contain at most one million items");
    double *copy = NULL;
    if (count) {
        copy = (double *)malloc(count * sizeof(double));
        if (!copy) fail_at(line, "out of memory");
        memcpy(copy, source.data, count * sizeof(double));
    }
    Variable *variable = &context->vars[index];
    free(variable->bytes);
    free(variable->array_data);
    variable->bytes = NULL;
    variable->array_data = copy;
    variable->array_length = count;
    variable->array_value = 1;
    variable->array_complex = 0;
    variable->matrix_rows = source.rows;
    variable->matrix_columns = source.columns;
    variable->initialized = 1;
    variable->killed = 0;
}

static CosmicMatrix cosmic_get_variable_matrix(Context *context, int index, int line) {
    Variable *variable = &context->vars[index];
    if (!variable->initialized || variable->killed)
        fail_at(line, "matrix is not declared or is empty");
    if (variable->matrix_rows == 0 || variable->matrix_columns == 0)
        fail_at(line, "value is not a matrix");
    CosmicMatrix matrix = {
        (double *)variable->array_data, variable->matrix_rows, variable->matrix_columns
    };
    return matrix;
}
static CosmicArray cosmic_array_set(CosmicArray array, double index, double value, int line) {
    size_t position = cosmic_array_index(array, index, line, 0);
    double *data = cosmic_allocate_array(array.length, line);
    if (array.length) memcpy(data, array.data, array.length * sizeof(double));
    data[position] = value;
    return cosmic_array(data, array.length);
}

static CosmicArray cosmic_array_push(CosmicArray array, double value, int line) {
    if (array.length >= 1000000) fail_at(line, "arrays may contain at most one million items");
    double *data = cosmic_allocate_array(array.length + 1, line);
    if (array.length) memcpy(data, array.data, array.length * sizeof(double));
    data[array.length] = value;
    return cosmic_array(data, array.length + 1);
}

static CosmicArray cosmic_array_set_variable(
    Context *context, int index, double position, double value, int line
) {
    CosmicArray current = cosmic_get_array(context, index, line);
    size_t array_index = cosmic_array_index(current, position, line, 0);
    context->arrays[index].data[array_index] = value;
    return cosmic_get_array(context, index, line);
}

static CosmicArray cosmic_array_push_variable(
    Context *context, int index, double value, int line
) {
    CosmicArray current = cosmic_get_array(context, index, line);
    if (current.length >= 1000000) fail_at(line, "arrays may contain at most one million items");
    double *data = (double *)realloc(
        context->arrays[index].data,
        (current.length + 1) * sizeof(double)
    );
    if (!data) fail_at(line, "out of memory");
    data[current.length] = value;
    context->arrays[index].data = data;
    context->arrays[index].length = current.length + 1;
    context->arrays[index].matrix_rows = 0;
    context->arrays[index].matrix_columns = 0;
    return cosmic_array(data, current.length + 1);
}

static CosmicArray cosmic_array_set_dynamic_variable(
    Context *context, int index, double position, double value, int line
) {
    CosmicArray current = cosmic_get_variable_array(context, index, line);
    size_t array_index = cosmic_array_index(current, position, line, 0);
    ((double *)context->vars[index].array_data)[array_index] = value;
    return cosmic_get_variable_array(context, index, line);
}

static CosmicArray cosmic_array_push_dynamic_variable(
    Context *context, int index, double value, int line
) {
    CosmicArray current = cosmic_get_variable_array(context, index, line);
    if (current.length >= 1000000) fail_at(line, "arrays may contain at most one million items");
    double *data = (double *)realloc(
        context->vars[index].array_data,
        (current.length + 1) * sizeof(double)
    );
    if (!data) fail_at(line, "out of memory");
    data[current.length] = value;
    context->vars[index].array_data = data;
    context->vars[index].array_length = current.length + 1;
    context->vars[index].matrix_rows = 0;
    context->vars[index].matrix_columns = 0;
    return cosmic_get_variable_array(context, index, line);
}

static size_t cosmic_complex_array_index(
    CosmicComplexArray array, double index, int line, int allow_end
) {
    if (!isfinite(index) || index != trunc(index) || index < 0 ||
        index > (double)array.length || (!allow_end && index == (double)array.length)) {
        fail_at(line, "array index is outside the array");
    }
    return (size_t)index;
}

static CosmicComplexArray cosmic_complex_array_set(
    CosmicComplexArray array, double index, double complex value, int line
) {
    size_t position = cosmic_complex_array_index(array, index, line, 0);
    double complex *data = cosmic_allocate_complex_array(array.length, line);
    if (array.length) memcpy(data, array.data, array.length * sizeof(double complex));
    data[position] = value;
    return cosmic_complex_array(data, array.length);
}

static CosmicComplexArray cosmic_complex_array_push(
    CosmicComplexArray array, double complex value, int line
) {
    if (array.length >= 1000000) fail_at(line, "arrays may contain at most one million items");
    double complex *data = cosmic_allocate_complex_array(array.length + 1, line);
    if (array.length) memcpy(data, array.data, array.length * sizeof(double complex));
    data[array.length] = value;
    return cosmic_complex_array(data, array.length + 1);
}

static CosmicComplexArray cosmic_complex_array_set_named(
    Context *context, int index, double position, double complex value, int line
) {
    CosmicComplexArray current = cosmic_get_complex_array(context, index, line);
    size_t array_index = cosmic_complex_array_index(current, position, line, 0);
    context->arrays[index].complex_data[array_index] = value;
    return cosmic_get_complex_array(context, index, line);
}

static CosmicComplexArray cosmic_complex_array_push_named(
    Context *context, int index, double complex value, int line
) {
    CosmicComplexArray current = cosmic_get_complex_array(context, index, line);
    if (current.length >= 1000000) fail_at(line, "arrays may contain at most one million items");
    double complex *data = (double complex *)realloc(
        context->arrays[index].complex_data,
        (current.length + 1) * sizeof(double complex)
    );
    if (!data) fail_at(line, "out of memory");
    data[current.length] = value;
    context->arrays[index].complex_data = data;
    context->arrays[index].length = current.length + 1;
    context->arrays[index].matrix_rows = 0;
    context->arrays[index].matrix_columns = 0;
    return cosmic_get_complex_array(context, index, line);
}

static CosmicComplexArray cosmic_complex_array_set_dynamic_variable(
    Context *context, int index, double position, double complex value, int line
) {
    CosmicComplexArray current = cosmic_get_variable_complex_array(context, index, line);
    size_t array_index = cosmic_complex_array_index(current, position, line, 0);
    ((double complex *)context->vars[index].array_data)[array_index] = value;
    return cosmic_get_variable_complex_array(context, index, line);
}

static CosmicComplexArray cosmic_complex_array_push_dynamic_variable(
    Context *context, int index, double complex value, int line
) {
    CosmicComplexArray current = cosmic_get_variable_complex_array(context, index, line);
    if (current.length >= 1000000) fail_at(line, "arrays may contain at most one million items");
    double complex *data = (double complex *)realloc(
        context->vars[index].array_data,
        (current.length + 1) * sizeof(double complex)
    );
    if (!data) fail_at(line, "out of memory");
    data[current.length] = value;
    context->vars[index].array_data = data;
    context->vars[index].array_length = current.length + 1;
    context->vars[index].matrix_rows = 0;
    context->vars[index].matrix_columns = 0;
    return cosmic_get_variable_complex_array(context, index, line);
}

static double cosmic_bytes_set(Context *context, int index, double position, double value, int line) {
    CosmicArray array = cosmic_get_array(context, index, line);
    size_t at = cosmic_array_index(array, position, line, 0);
    if (!isfinite(value) || value != trunc(value) || value < 0.0 || value > 255.0) {
        fail_at(line, "bytes values must be integers in 0..255");
    }
    context->arrays[index].data[at] = value;
    return value;
}

static double cosmic_bytes_get(Context *context, int index, double position, int line) {
    CosmicArray array = cosmic_get_array(context, index, line);
    size_t at = cosmic_array_index(array, position, line, 0);
    return array.data[at];
}

static double cosmic_array_at(CosmicArray array, double index, int line) {
    return array.data[cosmic_array_index(array, index, line, 0)];
}

static double cosmic_array_sum(CosmicArray array) {
    return vector_sum(array.data, array.length);
}

static double cosmic_array_dot(CosmicArray left, CosmicArray right, int line) {
    if (left.length != right.length) fail_at(line, "dot arrays must have equal lengths");
    return vector_dot(left.data, right.data, left.length);
}

static double cosmic_array_mean(CosmicArray array, int line) {
    if (!array.length) fail_at(line, "mean requires a non-empty array");
    return cosmic_array_sum(array) / (double)array.length;
}

static double cosmic_array_std(CosmicArray array, int line) {
    if (!array.length) fail_at(line, "std requires a non-empty array");
    double average = cosmic_array_mean(array, line);
    double total = 0.0;
    for (size_t i = 0; i < array.length; ++i) {
        double difference = array.data[i] - average;
        total += difference * difference;
    }
    return sqrt(total / (double)array.length);
}

static double cosmic_beta(double a, double b, int line) {
    if (!isfinite(a) || !isfinite(b)) fail_at(line, "beta requires finite arguments");
    return exp(lgamma(a) + lgamma(b) - lgamma(a + b));
}

static double cosmic_riemann_zeta(double s, int line) {
    if (!isfinite(s)) fail_at(line, "zeta requires a finite argument");
    if (s <= 1.0) fail_at(line, "zeta currently supports real arguments greater than 1");
    const int samples = 1000;
    double total = 0.0;
    for (int n = 1; n <= samples; ++n) total += pow((double)n, -s);
    double n = (double)samples;
    total += pow(n, 1.0 - s) / (s - 1.0);
    total -= 0.5 * pow(n, -s);
    total += (s / 12.0) * pow(n, -s - 1.0);
    total -= (s * (s + 1.0) * (s + 2.0) / 720.0) * pow(n, -s - 3.0);
    return total;
}

static int cosmic_compare_double(const void *left, const void *right) {
    double a = *(const double *)left;
    double b = *(const double *)right;
    if (a < b) return -1;
    if (a > b) return 1;
    return 0;
}

static double cosmic_array_median(CosmicArray array, int line) {
    if (!array.length) fail_at(line, "median requires a non-empty array");
    double *ordered = cosmic_allocate_array(array.length, line);
    memcpy(ordered, array.data, array.length * sizeof(double));
    qsort(ordered, array.length, sizeof(double), cosmic_compare_double);
    size_t middle = array.length / 2;
    if (array.length % 2) return ordered[middle];
    return (ordered[middle - 1] + ordered[middle]) / 2.0;
}

static double cosmic_array_variance(CosmicArray array, int line) {
    if (!array.length) fail_at(line, "variance requires a non-empty array");
    double average = cosmic_array_mean(array, line);
    double total = 0.0;
    for (size_t i = 0; i < array.length; ++i) {
        double difference = array.data[i] - average;
        total += difference * difference;
    }
    return total / (double)array.length;
}

static double cosmic_array_correlation(CosmicArray left, CosmicArray right, int line) {
    if (left.length != right.length) fail_at(line, "correlation arrays must have equal lengths");
    if (!left.length) fail_at(line, "correlation requires non-empty arrays");
    double left_mean = cosmic_array_mean(left, line);
    double right_mean = cosmic_array_mean(right, line);
    double numerator = 0.0;
    double left_variance = 0.0;
    double right_variance = 0.0;
    for (size_t i = 0; i < left.length; ++i) {
        double left_difference = left.data[i] - left_mean;
        double right_difference = right.data[i] - right_mean;
        numerator += left_difference * right_difference;
        left_variance += left_difference * left_difference;
        right_variance += right_difference * right_difference;
    }
    if (left_variance == 0.0 || right_variance == 0.0)
        fail_at(line, "correlation is undefined for a constant array");
    return numerator / sqrt(left_variance * right_variance);
}

static double cosmic_array_percentile(CosmicArray array, double percentage, int line) {
    if (!array.length) fail_at(line, "percentile requires a non-empty array");
    if (!isfinite(percentage) || percentage < 0.0 || percentage > 100.0)
        fail_at(line, "percentile percentage must be between 0 and 100");
    double *ordered = cosmic_allocate_array(array.length, line);
    memcpy(ordered, array.data, array.length * sizeof(double));
    qsort(ordered, array.length, sizeof(double), cosmic_compare_double);
    if (array.length == 1) return ordered[0];
    double position = (double)(array.length - 1) * percentage / 100.0;
    size_t lower = (size_t)floor(position);
    size_t upper = (size_t)ceil(position);
    if (lower == upper) return ordered[lower];
    double weight = position - (double)lower;
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight;
}

static double cosmic_array_trapz(CosmicArray array, double step, int line) {
    if (array.length < 2) fail_at(line, "trapz requires at least two samples");
    if (!isfinite(step) || step <= 0.0) fail_at(line, "trapz step must be positive");
    double total = (array.data[0] + array.data[array.length - 1]) / 2.0;
    for (size_t i = 1; i + 1 < array.length; ++i) total += array.data[i];
    return total * step;
}

static double cosmic_array_simpson(CosmicArray array, double step, int line) {
    if (array.length < 3 || array.length % 2 == 0)
        fail_at(line, "simpson requires an odd number of samples (at least three)");
    if (!isfinite(step) || step <= 0.0) fail_at(line, "simpson step must be positive");
    double total = array.data[0] + array.data[array.length - 1];
    for (size_t i = 1; i + 1 < array.length; i += 2) total += 4.0 * array.data[i];
    for (size_t i = 2; i + 1 < array.length; i += 2) total += 2.0 * array.data[i];
    return total * step / 3.0;
}

static double cosmic_array_min(CosmicArray array, int line) {
    if (!array.length) fail_at(line, "min requires a non-empty array");
    double value = array.data[0];
    for (size_t i = 1; i < array.length; ++i) if (array.data[i] < value) value = array.data[i];
    return value;
}

static double cosmic_array_max(CosmicArray array, int line) {
    if (!array.length) fail_at(line, "max requires a non-empty array");
    double value = array.data[0];
    for (size_t i = 1; i < array.length; ++i) if (array.data[i] > value) value = array.data[i];
    return value;
}

static double complex complex_vector_sum(const double complex *values, size_t count) {
    double complex total = 0.0;
    for (size_t i = 0; i < count; ++i) total += values[i];
    return total;
}

static double complex complex_vector_dot(const double complex *left, const double complex *right, size_t count) {
    double complex total = 0.0;
    for (size_t i = 0; i < count; ++i) total += left[i] * right[i];
    return total;
}

static double complex complex_vector_at(const double complex *values, size_t count, double index, int line) {
    if (!isfinite(index) || index != trunc(index) || index < 0 || index >= count) {
        fail_at(line, "array index is outside the array");
    }
    return values[(size_t)index];
}

static double complex cosmic_complex_array_sum(CosmicComplexArray array) {
    return complex_vector_sum(array.data, array.length);
}

static double complex cosmic_complex_array_at(
    CosmicComplexArray array, double index, int line
) {
    return complex_vector_at(array.data, array.length, index, line);
}

static double complex cosmic_complex_array_dot(
    CosmicComplexArray left, CosmicComplexArray right, int line
) {
    if (left.length != right.length) fail_at(line, "dot arrays must have equal lengths");
    return complex_vector_dot(left.data, right.data, left.length);
}

static double matrix2_mul_entry(const double *left, const double *right, double row, double column, int line) {
    if (!isfinite(row) || !isfinite(column) || row != trunc(row) || column != trunc(column) ||
        row < 0 || row > 1 || column < 0 || column > 1) {
        fail_at(line, "matrix row or column is outside the 2 by 2 matrix");
    }
    size_t r = (size_t)row, c = (size_t)column;
    return left[r * 2] * right[c] + left[r * 2 + 1] * right[2 + c];
}

static double complex complex_matrix2_mul_entry(const double complex *left, const double complex *right, double row, double column, int line) {
    if (!isfinite(row) || !isfinite(column) || row != trunc(row) || column != trunc(column) ||
        row < 0 || row > 1 || column < 0 || column > 1) {
        fail_at(line, "matrix row or column is outside the 2 by 2 matrix");
    }
    size_t r = (size_t)row, c = (size_t)column;
    return left[r * 2] * right[c] + left[r * 2 + 1] * right[2 + c];
}

static int64_t integer_variable(Context *context, int index, int line) {
    char *text = get_variable(context, index, line);
    if (!text) fail_at(line, "integer variable is empty or killed");
    char *end = NULL;
    errno = 0;
    long long value = strtoll(text, &end, 10);
    int invalid = errno || end == text || *end != '\0';
    free(text);
    if (invalid) fail_at(line, "variable does not contain an integer");
    return (int64_t)value;
}

static int64_t checked_add(int64_t left, int64_t right, int line) {
    if ((right > 0 && left > INT64_MAX - right) ||
        (right < 0 && left < INT64_MIN - right)) {
        fail_at(line, "int64 arithmetic overflow");
    }
    return left + right;
}

static int64_t checked_sub(int64_t left, int64_t right, int line) {
    if ((right < 0 && left > INT64_MAX + right) ||
        (right > 0 && left < INT64_MIN + right)) {
        fail_at(line, "int64 arithmetic overflow");
    }
    return left - right;
}

static int64_t checked_mul(int64_t left, int64_t right, int line) {
    if (left == 0 || right == 0) return 0;
    if ((left == -1 && right == INT64_MIN) || (right == -1 && left == INT64_MIN)) {
        fail_at(line, "int64 arithmetic overflow");
    }
    if (left > 0) {
        if ((right > 0 && left > INT64_MAX / right) ||
            (right < 0 && right < INT64_MIN / left)) {
            fail_at(line, "int64 arithmetic overflow");
        }
    } else {
        if ((right > 0 && left < INT64_MIN / right) ||
            (right < 0 && left < INT64_MAX / right)) {
            fail_at(line, "int64 arithmetic overflow");
        }
    }
    return left * right;
}

static int64_t checked_div(int64_t left, int64_t right, int line) {
    if (!right || (left == INT64_MIN && right == -1) || left % right != 0) {
        fail_at(line, "integer division is invalid or has a remainder");
    }
    return left / right;
}

static int64_t checked_floor_div(int64_t left, int64_t right, int line) {
    if (!right || (left == INT64_MIN && right == -1)) fail_at(line, "invalid integer division");
    int64_t quotient = left / right, remainder = left % right;
    if (remainder && ((remainder < 0) != (right < 0))) --quotient;
    return quotient;
}

static int64_t checked_mod(int64_t left, int64_t right, int line) {
    if (!right) fail_at(line, "modulo by zero");
    int64_t remainder = left % right;
    if (remainder && ((remainder < 0) != (right < 0))) remainder += right;
    return remainder;
}

static uint32_t u32_add(uint32_t left, uint32_t right, int line) { (void)line; return left + right; }
static uint32_t u32_sub(uint32_t left, uint32_t right, int line) { (void)line; return left - right; }
static uint32_t u32_mul(uint32_t left, uint32_t right, int line) { (void)line; return left * right; }
static uint32_t u32_div(uint32_t left, uint32_t right, int line) { if (!right) fail_at(line, "division by zero"); return left / right; }
static uint32_t u32_mod(uint32_t left, uint32_t right, int line) { if (!right) fail_at(line, "modulo by zero"); return left % right; }
static uint32_t u32_and(uint32_t left, uint32_t right, int line) { (void)line; return left & right; }
static uint32_t u32_or(uint32_t left, uint32_t right, int line) { (void)line; return left | right; }
static uint32_t u32_xor(uint32_t left, uint32_t right, int line) { (void)line; return left ^ right; }

static uint32_t u32_shift(uint32_t value, uint32_t count, int shift_left, int line) {
    if (count >= 32) fail_at(line, "shift count is too large for uint32");
    return shift_left ? (uint32_t)(value << count) : (uint32_t)(value >> count);
}

static uint32_t u32_pow(uint32_t base, uint32_t exponent, int line) {
    uint32_t result = 1;
    while (exponent) {
        if (exponent & 1U) result *= base;
        exponent >>= 1;
        if (exponent) base *= base;
    }
    return result;
}

static int64_t checked_bit_and(int64_t left, int64_t right, int line) {
    (void)line;
    return left & right;
}

static int64_t checked_bit_or(int64_t left, int64_t right, int line) {
    (void)line;
    return left | right;
}

static int64_t checked_bit_xor(int64_t left, int64_t right, int line) {
    (void)line;
    return left ^ right;
}

static int64_t checked_shift(int64_t value, int64_t count, int shift_left, int line) {
    if (count < 0) fail_at(line, "shift count must not be negative");
    if (count > 63) fail_at(line, "shift count is too large for int64");
    if (shift_left) {
        if (value != 0 && (value > (INT64_MAX >> count) || value < (INT64_MIN >> count))) {
            fail_at(line, "int64 arithmetic overflow");
        }
        return value << count;
    }
    return value >> count;
}

static int64_t checked_pow(int64_t base, int64_t exponent, int line) {
    if (exponent < 0) {
        if (base == 1) return 1;
        if (base == -1) return (exponent & 1) ? -1 : 1;
        fail_at(line, "negative integer exponent is not an integer result");
    }
    int64_t result = 1;
    while (exponent) {
        if (exponent & 1) result = checked_mul(result, base, line);
        exponent >>= 1;
        if (exponent) base = checked_mul(base, base, line);
    }
    return result;
}

typedef struct {
    int sign;
    size_t length;
    uint32_t *limbs;
} BigInt;
typedef struct BigIntAllocation {
    uint32_t *limbs;
    struct BigIntAllocation *next;
} BigIntAllocation;
static BigIntAllocation *bigint_allocations;

static void bigint_release_all(void) {
    while (bigint_allocations) {
        BigIntAllocation *allocation = bigint_allocations;
        bigint_allocations = allocation->next;
        free(allocation->limbs);
        free(allocation);
    }
}

static BigInt bigint_zero(void) {
    BigInt value = {0, 0, NULL};
    return value;
}

static BigInt bigint_allocate(size_t length, int line) {
    BigInt value = bigint_zero();
    if (!length) return value;
    if (length > 31251) fail_at(line, "integer result is too large");
    value.limbs = (uint32_t *)calloc(length, sizeof(uint32_t));
    BigIntAllocation *allocation = (BigIntAllocation *)malloc(sizeof(BigIntAllocation));
    if (!value.limbs || !allocation) {
        free(value.limbs);
        free(allocation);
        fail_at(line, "out of memory");
    }
    allocation->limbs = value.limbs;
    allocation->next = bigint_allocations;
    bigint_allocations = allocation;
    value.length = length;
    return value;
}

static void bigint_normalize(BigInt *value) {
    while (value->length && value->limbs[value->length - 1] == 0) --value->length;
    if (!value->length) value->sign = 0;
}

static void bigint_check_size(BigInt value, int line) {
    if (!value.length) return;
    uint32_t top = value.limbs[value.length - 1];
    unsigned int top_bits = 0;
    while (top) { ++top_bits; top >>= 1; }
    if ((value.length - 1) * 32 + top_bits > 1000000) {
        fail_at(line, "integer result is too large");
    }
}

static BigInt bigint_from_int64(int64_t number, int line) {
    if (!number) return bigint_zero();
    uint64_t magnitude = number < 0
        ? (uint64_t)(-(number + 1)) + 1 : (uint64_t)number;
    BigInt value = bigint_allocate(magnitude > UINT32_MAX ? 2 : 1, line);
    value.sign = number < 0 ? -1 : 1;
    value.limbs[0] = (uint32_t)magnitude;
    if (value.length > 1) value.limbs[1] = (uint32_t)(magnitude >> 32);
    bigint_normalize(&value);
    return value;
}

static BigInt bigint_parse(const char *text, int line) {
    if (!text || !*text) fail_at(line, "variable does not contain an integer");
    int sign = 1;
    if (*text == '+' || *text == '-') {
        if (*text++ == '-') sign = -1;
        if (!*text) fail_at(line, "variable does not contain an integer");
    }
    size_t digits = strlen(text);
    size_t capacity = (digits / 32000) * 3322 +
        ((digits % 32000) * 3322 + 31999) / 32000 + 1;
    if (capacity > 31251) capacity = 31251;
    BigInt value = bigint_allocate(capacity, line);
    value.length = 0;
    value.sign = 1;
    for (const char *cursor = text; *cursor; ++cursor) {
        if (*cursor < '0' || *cursor > '9') fail_at(line, "variable does not contain an integer");
        uint64_t carry = (uint32_t)(*cursor - '0');
        for (size_t i = 0; i < value.length; ++i) {
            uint64_t product = (uint64_t)value.limbs[i] * 10 + carry;
            value.limbs[i] = (uint32_t)product;
            carry = product >> 32;
        }
        if (carry) {
            if (value.length >= capacity) fail_at(line, "integer result is too large");
            value.limbs[value.length++] = (uint32_t)carry;
        }
        if (!value.length && carry) value.length = 1;
    }
    if (!value.length) value.sign = 0;
    else value.sign = sign;
    bigint_check_size(value, line);
    return value;
}

static BigInt bigint_variable(Context *context, int index, int line) {
    char *text = get_variable(context, index, line);
    if (!text) fail_at(line, "integer variable is empty or killed");
    BigInt result = bigint_parse(text, line);
    free(text);
    return result;
}

static int64_t bigint_to_int64(BigInt value, int line) {
    if (value.length > 2) fail_at(line, "integer is outside int64 range");
    uint64_t magnitude = value.length ? value.limbs[0] : 0;
    if (value.length > 1) magnitude |= (uint64_t)value.limbs[1] << 32;
    if (value.sign < 0) {
        if (magnitude > (uint64_t)INT64_MAX + 1U) fail_at(line, "integer is outside int64 range");
        if (magnitude == (uint64_t)INT64_MAX + 1U) return INT64_MIN;
        return -(int64_t)magnitude;
    }
    if (magnitude > (uint64_t)INT64_MAX) fail_at(line, "integer is outside int64 range");
    return (int64_t)magnitude;
}

static int bigint_compare_magnitude(BigInt left, BigInt right) {
    if (left.length != right.length) return left.length < right.length ? -1 : 1;
    for (size_t i = left.length; i-- > 0;) {
        if (left.limbs[i] != right.limbs[i]) return left.limbs[i] < right.limbs[i] ? -1 : 1;
    }
    return 0;
}

static int bigint_compare(BigInt left, BigInt right) {
    if (left.sign != right.sign) return left.sign < right.sign ? -1 : 1;
    if (!left.sign) return 0;
    int comparison = bigint_compare_magnitude(left, right);
    return left.sign < 0 ? -comparison : comparison;
}

static BigInt bigint_add_magnitude(BigInt left, BigInt right, int line) {
    size_t length = left.length > right.length ? left.length : right.length;
    BigInt result = bigint_allocate(length + 1, line);
    uint64_t carry = 0;
    for (size_t i = 0; i < length; ++i) {
        uint64_t sum = carry;
        if (i < left.length) sum += left.limbs[i];
        if (i < right.length) sum += right.limbs[i];
        result.limbs[i] = (uint32_t)sum;
        carry = sum >> 32;
    }
    result.limbs[length] = (uint32_t)carry;
    result.sign = 1;
    bigint_normalize(&result);
    bigint_check_size(result, line);
    return result;
}

static BigInt bigint_subtract_magnitude(BigInt larger, BigInt smaller, int line) {
    BigInt result = bigint_allocate(larger.length, line);
    int64_t borrow = 0;
    for (size_t i = 0; i < larger.length; ++i) {
        int64_t difference = (int64_t)larger.limbs[i] -
            (i < smaller.length ? (int64_t)smaller.limbs[i] : 0) - borrow;
        result.limbs[i] = (uint32_t)difference;
        borrow = difference < 0;
    }
    result.sign = 1;
    bigint_normalize(&result);
    return result;
}

static BigInt bigint_negate(BigInt value) {
    value.sign = -value.sign;
    return value;
}

static BigInt bigint_add(BigInt left, BigInt right, int line) {
    if (!left.sign) return right;
    if (!right.sign) return left;
    if (left.sign == right.sign) {
        BigInt result = bigint_add_magnitude(left, right, line);
        result.sign = left.sign;
        return result;
    }
    int comparison = bigint_compare_magnitude(left, right);
    if (!comparison) return bigint_zero();
    BigInt result = comparison > 0
        ? bigint_subtract_magnitude(left, right, line)
        : bigint_subtract_magnitude(right, left, line);
    result.sign = comparison > 0 ? left.sign : right.sign;
    return result;
}

static BigInt bigint_subtract(BigInt left, BigInt right, int line) {
    return bigint_add(left, bigint_negate(right), line);
}

static BigInt bigint_multiply(BigInt left, BigInt right, int line) {
    if (!left.sign || !right.sign) return bigint_zero();
    if (left.length + right.length > 31252) fail_at(line, "integer result is too large");
    BigInt result = bigint_allocate(left.length + right.length, line);
    result.sign = left.sign * right.sign;
    for (size_t i = 0; i < left.length; ++i) {
        uint64_t carry = 0;
        for (size_t j = 0; j < right.length; ++j) {
            uint64_t product = (uint64_t)left.limbs[i] * right.limbs[j] +
                result.limbs[i + j] + carry;
            result.limbs[i + j] = (uint32_t)product;
            carry = product >> 32;
        }
        size_t position = i + right.length;
        while (carry && position < result.length) {
            uint64_t sum = (uint64_t)result.limbs[position] + carry;
            result.limbs[position++] = (uint32_t)sum;
            carry = sum >> 32;
        }
    }
    bigint_normalize(&result);
    bigint_check_size(result, line);
    return result;
}

static unsigned int bigint_bit_length(BigInt value) {
    if (!value.length) return 0;
    uint32_t top = value.limbs[value.length - 1];
    unsigned int bits = 0;
    while (top) { ++bits; top >>= 1; }
    return (unsigned int)((value.length - 1) * 32 + bits);
}

static int bigint_get_bit(BigInt value, size_t position) {
    size_t limb = position / 32;
    return limb < value.length
        ? (int)((value.limbs[limb] >> (position % 32)) & 1U) : 0;
}

static BigInt bigint_divide_magnitude(
    BigInt dividend, BigInt divisor, BigInt *remainder, int line
) {
    if (!divisor.length) fail_at(line, "integer division by zero");
    BigInt quotient = bigint_allocate(dividend.length, line);
    quotient.sign = 1;
    BigInt rest = bigint_allocate(divisor.length + 1, line);
    rest.length = 0;
    rest.sign = 0;
    unsigned int bits = bigint_bit_length(dividend);
    for (unsigned int position = bits; position-- > 0;) {
        uint64_t carry = (uint64_t)bigint_get_bit(dividend, position);
        for (size_t i = 0; i < rest.length; ++i) {
            uint64_t shifted = ((uint64_t)rest.limbs[i] << 1) | carry;
            rest.limbs[i] = (uint32_t)shifted;
            carry = shifted >> 32;
        }
        if (carry) {
            if (rest.length >= divisor.length + 1) fail_at(line, "integer division overflow");
            rest.limbs[rest.length++] = (uint32_t)carry;
        }
        if (rest.length) rest.sign = 1;
        if (bigint_compare_magnitude(rest, divisor) >= 0) {
            int64_t borrow = 0;
            for (size_t i = 0; i < rest.length; ++i) {
                int64_t difference = (int64_t)rest.limbs[i] -
                    (i < divisor.length ? (int64_t)divisor.limbs[i] : 0) - borrow;
                rest.limbs[i] = (uint32_t)difference;
                borrow = difference < 0;
            }
            bigint_normalize(&rest);
            quotient.limbs[position / 32] |= (uint32_t)1U << (position % 32);
        }
    }
    bigint_normalize(&quotient);
    bigint_normalize(&rest);
    *remainder = rest;
    return quotient;
}

static BigInt bigint_floor_divide(BigInt left, BigInt right, int line) {
    BigInt remainder;
    BigInt quotient = bigint_divide_magnitude(left, right, &remainder, line);
    if (!quotient.length) return quotient;
    int negative = left.sign != right.sign;
    if (negative && remainder.length) {
        BigInt one = bigint_from_int64(1, line);
        quotient = bigint_add_magnitude(quotient, one, line);
    }
    quotient.sign = negative ? -1 : 1;
    return quotient;
}

static BigInt bigint_modulo(BigInt left, BigInt right, int line) {
    BigInt remainder;
    (void)bigint_divide_magnitude(left, right, &remainder, line);
    if (!remainder.length) return remainder;
    if (left.sign != right.sign) {
        BigInt divisor = right;
        divisor.sign = 1;
        remainder = bigint_subtract_magnitude(divisor, remainder, line);
    }
    remainder.sign = right.sign;
    return remainder;
}

static BigInt bigint_power(BigInt base, BigInt exponent, int line) {
    if (exponent.length > 1 || (exponent.length && exponent.limbs[0] > 10000)) {
        fail_at(line, "exponent is too large");
    }
    if (exponent.sign < 0) fail_at(line, "bigint mode requires integer-only expressions");
    uint32_t power = exponent.length ? exponent.limbs[0] : 0;
    if (power && (uint64_t)(bigint_bit_length(base) ? bigint_bit_length(base) : 1) * power > 1000000) {
        fail_at(line, "integer result is too large");
    }
    BigInt result = bigint_from_int64(1, line);
    while (power) {
        if (power & 1U) result = bigint_multiply(result, base, line);
        power >>= 1;
        if (power) base = bigint_multiply(base, base, line);
    }
    return result;
}

/* Write value's two's complement representation into bits[0..width-1]. */
static void bigint_to_twos_complement(BigInt value, unsigned char *bits, size_t width) {
    /* For negatives: (~magnitude) + 1, computed bitwise over the full width. */
    int negative = value.sign < 0;
    for (size_t i = 0; i < width; ++i) {
        size_t limb = i / 32;
        unsigned int raw = 0;
        if (limb < value.length) raw = (value.limbs[limb] >> (i % 32)) & 1U;
        bits[i] = negative ? (unsigned char)(raw ^ 1U) : (unsigned char)raw;
    }
    if (negative) {
        unsigned int carry = 1;
        for (size_t i = 0; i < width && carry; ++i) {
            unsigned int sum = (unsigned int)bits[i] + carry;
            bits[i] = (unsigned char)(sum & 1U);
            carry = sum >> 1;
        }
    }
}

static BigInt bigint_from_twos_complement(const unsigned char *bits, size_t width, int line) {
    int negative = bits[width - 1] != 0;
    unsigned char *work = (unsigned char *)malloc(width);
    if (!work) fail_at(line, "out of memory");
    memcpy(work, bits, width);
    if (negative) {
        /* magnitude = (~x + 1) over the full width */
        unsigned int carry = 1;
        for (size_t i = 0; i < width; ++i) {
            unsigned int sum = (unsigned int)(work[i] ^ 1U) + carry;
            work[i] = (unsigned char)(sum & 1U);
            carry = sum >> 1;
        }
    }
    BigInt value = bigint_allocate(width / 32 + 1, line);
    value.sign = 1;
    value.length = 0;
    for (size_t i = 0; i < width; ++i) {
        if (!work[i]) continue;
        size_t limb = i / 32;
        if (limb >= value.length) value.length = limb + 1;
        value.limbs[limb] |= (uint32_t)1U << (i % 32);
    }
    free(work);
    bigint_normalize(&value);
    if (value.length && negative) value.sign = -1;
    bigint_check_size(value, line);
    return value;
}

static BigInt bigint_bitwise(BigInt left, BigInt right, int operation, int line) {
    if (left.length > 31251 || right.length > 31251) fail_at(line, "integer result is too large");
    size_t width = 64;
    {
        size_t needed = left.length > right.length ? left.length : right.length;
        needed = needed * 32 + 64;
        if (needed > width) width = needed;
    }
    unsigned char *lbits = (unsigned char *)malloc(width);
    unsigned char *rbits = (unsigned char *)malloc(width);
    if (!lbits || !rbits) {
        free(lbits);
        free(rbits);
        fail_at(line, "out of memory");
    }
    bigint_to_twos_complement(left, lbits, width);
    bigint_to_twos_complement(right, rbits, width);
    for (size_t i = 0; i < width; ++i) {
        if (operation == 0) lbits[i] = lbits[i] & rbits[i];
        else if (operation == 1) lbits[i] = (unsigned char)(lbits[i] | rbits[i]);
        else lbits[i] = (unsigned char)(lbits[i] ^ rbits[i]);
    }
    BigInt result = bigint_from_twos_complement(lbits, width, line);
    free(lbits);
    free(rbits);
    return result;
}

static BigInt bigint_and(BigInt left, BigInt right, int line) {
    return bigint_bitwise(left, right, 0, line);
}

static BigInt bigint_or(BigInt left, BigInt right, int line) {
    return bigint_bitwise(left, right, 1, line);
}

static BigInt bigint_xor(BigInt left, BigInt right, int line) {
    return bigint_bitwise(left, right, 2, line);
}

static BigInt bigint_invert(BigInt value, int line) {
    return bigint_subtract(bigint_from_int64(0, line), bigint_add(value, bigint_from_int64(1, line), line), line);
}

static BigInt bigint_shift_left(BigInt value, size_t amount, int line) {
    if (!value.length || !amount) return value;
    size_t whole = amount / 32, part = amount % 32;
    if ((bigint_bit_length(value) + amount) > 1000000) fail_at(line, "integer result is too large");
    BigInt result = bigint_allocate(value.length + whole + 2, line);
    result.sign = value.sign;
    for (size_t i = 0; i < value.length; ++i) {
        uint64_t shifted = ((uint64_t)value.limbs[i]) << part;
        result.limbs[i + whole] |= (uint32_t)shifted;
        if (part) result.limbs[i + whole + 1] |= (uint32_t)(shifted >> 32);
    }
    result.length = value.length + whole + (part ? 1 : 0);
    bigint_normalize(&result);
    bigint_check_size(result, line);
    return result;
}

static BigInt bigint_shift_right(BigInt value, size_t amount, int line) {
    if (!value.length || !amount) return value;
    if (value.sign < 0) {
        /* arithmetic shift: floor division by 2^amount */
        BigInt divisor = bigint_shift_left(bigint_from_int64(1, line), amount, line);
        return bigint_floor_divide(value, divisor, line);
    }
    size_t whole = amount / 32;
    size_t part = amount % 32;
    if (whole >= value.length) return bigint_zero();
    BigInt result = bigint_allocate(value.length - whole, line);
    result.sign = 1;
    for (size_t i = whole; i < value.length; ++i) {
        uint64_t chunk = value.limbs[i];
        if (part && i + 1 < value.length) chunk |= ((uint64_t)value.limbs[i + 1]) << 32;
        result.limbs[i - whole] = (uint32_t)(chunk >> part);
    }
    bigint_normalize(&result);
    return result;
}

static BigInt bigint_shift(BigInt value, BigInt count, int shift_left, int line) {
    if (count.sign < 0) fail_at(line, "shift count must not be negative");
    if (count.length > 1 || (count.length && count.limbs[0] > 1000000)) {
        fail_at(line, "shift count is too large");
    }
    size_t amount = count.length ? count.limbs[0] : 0;
    return shift_left ? bigint_shift_left(value, amount, line)
                      : bigint_shift_right(value, amount, line);
}

static char *cosmic_hex(long long value, int width, int line) {
    char digits[32];
    int negative = value < 0;
    unsigned long long magnitude = negative ? (unsigned long long)(-(value + 1)) + 1ULL
                                            : (unsigned long long)value;
    int length = snprintf(digits, sizeof(digits), "%llx", magnitude);
    int total = length + (negative ? 1 : 0);
    if (width > 0 && length > width) fail_at(line, "hex value does not fit the requested width");
    int pad = (width > 0 ? width : length) - length;
    char *text = (char *)malloc((size_t)total + (size_t)pad + 1);
    if (!text) fail_at(line, "out of memory");
    size_t offset = 0;
    if (negative) text[offset++] = '-';
    for (int i = 0; i < pad; ++i) text[offset++] = '0';
    memcpy(text + offset, digits, (size_t)length);
    offset += (size_t)length;
    text[offset] = '\0';
    return text;
}

static BigInt bigint_from_hex(const char *text, int line) {
    if (!text) fail_at(line, "unhex expects a hexadecimal string");
    const char *cursor = text;
    int negative = 0;
    if (*cursor == '-') { negative = 1; ++cursor; }
    if (!*cursor) fail_at(line, "unhex requires hexadecimal digits");
    size_t digits = strlen(cursor);
    /* digits * 4 bits -> limbs, plus one guard limb */
    size_t capacity = (digits * 4) / 32 + 2;
    if (capacity > 31251) fail_at(line, "integer result is too large");
    BigInt value = bigint_allocate(capacity, line);
    value.sign = 1;
    value.length = 0;
    for (; *cursor; ++cursor) {
        int digit;
        if (*cursor >= '0' && *cursor <= '9') digit = *cursor - '0';
        else if (*cursor >= 'a' && *cursor <= 'f') digit = *cursor - 'a' + 10;
        else if (*cursor >= 'A' && *cursor <= 'F') digit = *cursor - 'A' + 10;
        else fail_at(line, "unhex requires hexadecimal digits");
        uint64_t carry = (uint64_t)digit;
        for (size_t i = 0; i < value.length; ++i) {
            uint64_t product = (uint64_t)value.limbs[i] * 16ULL + carry;
            value.limbs[i] = (uint32_t)product;
            carry = product >> 32;
        }
        if (carry) {
            if (value.length >= capacity) fail_at(line, "integer result is too large");
            value.limbs[value.length++] = (uint32_t)carry;
        }
        if (!value.length && digit) value.length = 1;
    }
    bigint_normalize(&value);
    if (value.length) value.sign = negative ? -1 : 1;
    bigint_check_size(value, line);
    return value;
}

static char *bigint_to_hex(BigInt value, int width, int line) {
    if (!value.length) {
        int pad = width > 0 ? width : 1;
        char *zero = (char *)malloc((size_t)pad + 1);
        if (!zero) fail_at(line, "out of memory");
        memset(zero, '0', (size_t)pad);
        zero[pad] = '\0';
        return zero;
    }
    size_t nibbles = (bigint_bit_length(value) + 3) / 4;
    int negative = value.sign < 0;
    if (width > 0 && nibbles > (size_t)width) {
        fail_at(line, "hex value does not fit the requested width");
    }
    size_t pad = (width > 0 && (size_t)width > nibbles) ? (size_t)width - nibbles : 0;
    char *text = (char *)malloc(nibbles + pad + (negative ? 2 : 1));
    if (!text) fail_at(line, "out of memory");
    size_t offset = 0;
    if (negative) text[offset++] = '-';
    for (size_t i = 0; i < pad; ++i) text[offset++] = '0';
    static const char *digits = "0123456789abcdef";
    for (size_t i = nibbles; i-- > 0;) {
        size_t bit = i * 4;
        unsigned int nibble = 0;
        for (size_t k = 0; k < 4; ++k) {
            size_t position = bit + k;
            size_t limb = position / 32;
            unsigned int raw = limb < value.length
                ? (value.limbs[limb] >> (position % 32)) & 1U : 0;
            nibble |= raw << k;
        }
        text[offset++] = digits[nibble];
    }
    text[offset] = '\0';
    return text;
}

static BigInt bigint_absolute(BigInt value) {
    if (value.sign < 0) value.sign = 1;
    return value;
}

static char *bigint_to_string(BigInt value, int line) {
    if (!value.length) return copy_text("0");
    uint32_t *work = (uint32_t *)malloc(value.length * sizeof(uint32_t));
    uint32_t *parts = (uint32_t *)malloc((value.length * 2 + 1) * sizeof(uint32_t));
    if (!work || !parts) {
        free(work);
        free(parts);
        fail_at(line, "out of memory");
    }
    memcpy(work, value.limbs, value.length * sizeof(uint32_t));
    size_t length = value.length, part_count = 0;
    while (length) {
        uint64_t remainder = 0;
        for (size_t i = length; i-- > 0;) {
            uint64_t current = (remainder << 32) | work[i];
            work[i] = (uint32_t)(current / 1000000000U);
            remainder = current % 1000000000U;
        }
        parts[part_count++] = (uint32_t)remainder;
        while (length && !work[length - 1]) --length;
    }
    size_t capacity = part_count * 9 + 3;
    char *text = (char *)malloc(capacity);
    if (!text) {
        free(work);
        free(parts);
        fail_at(line, "out of memory");
    }
    size_t offset = 0;
    if (value.sign < 0) text[offset++] = '-';
    offset += (size_t)snprintf(text + offset, capacity - offset, "%u", parts[part_count - 1]);
    while (--part_count) {
        offset += (size_t)snprintf(text + offset, capacity - offset, "%09u", parts[part_count - 1]);
    }
    free(work);
    free(parts);
    return text;
}

static void print_value(const char *value, const char *encoding) {
    if (!value) {
        fputc('\n', stdout);
        fflush(stdout);
        return;
    }
#ifdef _WIN32
    unsigned char *bytes = encode_value(value, encoding ? encoding : "UTF-8");
    if (bytes) {
        fwrite(bytes, 1, strlen((const char *)bytes), stdout);
        fputc('\n', stdout);
        free(bytes);
    }
#else
    (void)encoding;
    fputs(value, stdout);
    fputc('\n', stdout);
#endif
    fflush(stdout);
}

static char *run_command(const char *command, int *exit_status, int line) {
#ifdef _WIN32
    if (_stricmp(command, "dir") == 0 || _stricmp(command, "get-childitem") == 0) {
        command = "powershell.exe -NoProfile -NonInteractive -Command \"[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); Get-ChildItem\"";
    }
#endif
    FILE *pipe = popen(command, "r");
    if (!pipe) fail_at(line, "could not start external command");
    size_t capacity = 256, length = 0;
    char *output = (char *)malloc(capacity);
    if (!output) fail_at(line, "out of memory");
    int ch;
    while ((ch = fgetc(pipe)) != EOF) {
        if (length + 1 >= capacity) {
            capacity *= 2;
            char *grown = (char *)realloc(output, capacity);
            if (!grown) fail_at(line, "out of memory");
            output = grown;
        }
        output[length++] = (char)ch;
    }
    output[length] = '\0';
    *exit_status = pclose(pipe);
#ifdef _WIN32
    if (*exit_status != -1) *exit_status = (int)(((unsigned int)*exit_status >> 8) & 0xff);
#endif
    while (length && (output[length - 1] == '\r' || output[length - 1] == '\n')) {
        output[--length] = '\0';
    }
    return output;
}

static void tape_create(int index) {
    if (tapes[index].initialized) {
        TapeCell *cell = tapes[index].current;
        while (cell->left) cell = cell->left;
        while (cell) {
            TapeCell *next = cell->right;
            free(cell);
            cell = next;
        }
    }
    TapeCell *cell = (TapeCell *)calloc(1, sizeof(TapeCell));
    if (!cell) fail_at(0, "out of memory");
    tapes[index].current = cell;
    tapes[index].initialized = 1;
}

static Tape *get_tape(int index, int line) {
    if (!tapes[index].initialized) fail_at(line, "tape is not declared");
    return &tapes[index];
}

static void tape_move(int index, int direction, int line) {
    Tape *tape = get_tape(index, line);
    TapeCell **destination = direction < 0 ? &tape->current->left : &tape->current->right;
    if (!*destination) {
        *destination = (TapeCell *)calloc(1, sizeof(TapeCell));
        if (!*destination) fail_at(line, "out of memory");
        if (direction < 0) (*destination)->right = tape->current;
        else (*destination)->left = tape->current;
    }
    tape->current = *destination;
}

static int tape_read_cell(int index, int line) {
    Tape *tape = get_tape(index, line);
    return tape->current->symbol;
}

static void tape_write_cell(int index, int symbol, int line) {
    Tape *tape = get_tape(index, line);
    tape->current->symbol = symbol;
}

static void free_tapes(void) {
    for (int i = 0; i < TAPE_COUNT; ++i) {
        if (!tapes[i].initialized) continue;
        TapeCell *cell = tapes[i].current;
        while (cell->left) cell = cell->left;
        while (cell) {
            TapeCell *next = cell->right;
            free(cell);
            cell = next;
        }
        tapes[i].current = NULL;
        tapes[i].initialized = 0;
    }
}

static int random_die(double maximum, double minimum, int line) {
    if (!isfinite(minimum) || !isfinite(maximum) ||
        minimum != trunc(minimum) || maximum != trunc(maximum) ||
        minimum < INT_MIN || maximum > INT_MAX || minimum > maximum) {
        fail_at(line, "rag requires int32 bounds with min <= max");
    }

    static int seeded = 0;
    if (!seeded) {
        srand((unsigned int)(time(NULL) ^ clock() ^ (uintptr_t)&seeded));
        seeded = 1;
    }

    uint64_t base = (uint64_t)RAND_MAX + 1;
    uint64_t bound = (uint64_t)((int64_t)maximum - (int64_t)minimum) + 1;
    uint64_t range = base;
    int digits = 1;
    while (range < bound && range <= UINT64_MAX / base) {
        range *= base;
        ++digits;
    }
    if (range < bound) fail_at(line, "random range is unsupported");
    uint64_t limit = range - range % bound;
    uint64_t sample;
    do {
        sample = 0;
        for (int i = 0; i < digits; ++i) {
            sample = sample * base + (uint64_t)rand();
        }
    } while (sample >= limit);
    return (int)((int64_t)minimum + (int64_t)(sample % bound));
}

static char *unicode_chr(double codepoint, int line) {
    if (!isfinite(codepoint) || codepoint != trunc(codepoint) ||
        codepoint <= 0 || codepoint > 0x10ffff ||
        (codepoint >= 0xd800 && codepoint <= 0xdfff)) {
        fail_at(line, "chr code point must be a valid Unicode scalar value");
    }

    uint32_t value = (uint32_t)codepoint;
    char *text = (char *)calloc(5, sizeof(char));
    if (!text) fail_at(line, "out of memory");
    if (value <= 0x7f) {
        text[0] = (char)value;
    } else if (value <= 0x7ff) {
        text[0] = (char)(0xc0 | (value >> 6));
        text[1] = (char)(0x80 | (value & 0x3f));
    } else if (value <= 0xffff) {
        text[0] = (char)(0xe0 | (value >> 12));
        text[1] = (char)(0x80 | ((value >> 6) & 0x3f));
        text[2] = (char)(0x80 | (value & 0x3f));
    } else {
        text[0] = (char)(0xf0 | (value >> 18));
        text[1] = (char)(0x80 | ((value >> 12) & 0x3f));
        text[2] = (char)(0x80 | ((value >> 6) & 0x3f));
        text[3] = (char)(0x80 | (value & 0x3f));
    }
    return text;
}

static int unicode_ord(const char *text, int line) {
    if (!text) fail_at(line, "ord requires exactly one Unicode character");
    const unsigned char *bytes = (const unsigned char *)text;
    size_t length = strlen(text);
    uint32_t value;
    size_t width;
    if (length == 0) {
        fail_at(line, "ord requires exactly one Unicode character");
    } else if (bytes[0] <= 0x7f) {
        value = bytes[0];
        width = 1;
    } else if (bytes[0] >= 0xc2 && bytes[0] <= 0xdf) {
        value = bytes[0] & 0x1f;
        width = 2;
    } else if (bytes[0] >= 0xe0 && bytes[0] <= 0xef) {
        value = bytes[0] & 0x0f;
        width = 3;
    } else if (bytes[0] >= 0xf0 && bytes[0] <= 0xf4) {
        value = bytes[0] & 0x07;
        width = 4;
    } else {
        fail_at(line, "ord input is not valid UTF-8");
    }

    if (length != width) {
        fail_at(line, "ord requires exactly one Unicode character");
    }
    for (size_t i = 1; i < width; ++i) {
        if ((bytes[i] & 0xc0) != 0x80) {
            fail_at(line, "ord input is not valid UTF-8");
        }
        value = (value << 6) | (bytes[i] & 0x3f);
    }
    if ((width == 2 && value < 0x80) ||
        (width == 3 && value < 0x800) ||
        (width == 4 && value < 0x10000) ||
        value > 0x10ffff || (value >= 0xd800 && value <= 0xdfff)) {
        fail_at(line, "ord input is not a valid Unicode scalar value");
    }
    return (int)value;
}

static int unicode_ord_owned(char *text, int line) {
    int value = unicode_ord(text, line);
    free(text);
    return value;
}

static size_t unicode_strlen(const char *text) {
    size_t count = 0;
    for (const unsigned char *cursor = (const unsigned char *)text; *cursor; ++cursor) {
        if ((*cursor & 0xc0) != 0x80) ++count;
    }
    return count;
}

static size_t unicode_strlen_owned(char *text) {
    size_t count = unicode_strlen(text);
    free(text);
    return count;
}

static char *unicode_char_at(const char *text, double index, int line) {
    if (!isfinite(index) || index != trunc(index) || index < 0) {
        fail_at(line, "char_at index must be a non-negative integer");
    }
    size_t wanted = (size_t)index;
    size_t current = 0;
    const unsigned char *cursor = (const unsigned char *)text;
    while (*cursor && current < wanted) {
        size_t current_width = 1;
        if ((*cursor & 0xe0) == 0xc0) current_width = 2;
        else if ((*cursor & 0xf0) == 0xe0) current_width = 3;
        else if ((*cursor & 0xf8) == 0xf0) current_width = 4;
        cursor += current_width;
        ++current;
    }
    if (!*cursor) fail_at(line, "char_at index is outside the string");

    size_t width = 1;
    if ((*cursor & 0xe0) == 0xc0) width = 2;
    else if ((*cursor & 0xf0) == 0xe0) width = 3;
    else if ((*cursor & 0xf8) == 0xf0) width = 4;
    char *character = (char *)calloc(width + 1, sizeof(char));
    if (!character) fail_at(line, "out of memory");
    memcpy(character, cursor, width);
    return character;
}

static char *unicode_char_at_owned(char *text, double index, int line) {
    char *character = unicode_char_at(text, index, line);
    free(text);
    return character;
}

static int valid_utf8(const unsigned char *text, size_t length) {
    size_t i = 0;
    while (i < length) {
        unsigned char first = text[i];
        if (first < 0x80) {
            ++i;
            continue;
        }
        size_t width = first >= 0xc2 && first <= 0xdf ? 2 :
                       first >= 0xe0 && first <= 0xef ? 3 :
                       first >= 0xf0 && first <= 0xf4 ? 4 : 0;
        if (!width || i + width > length) return 0;
        for (size_t j = 1; j < width; ++j) {
            if ((text[i + j] & 0xc0) != 0x80) return 0;
        }
        if ((first == 0xe0 && text[i + 1] < 0xa0) ||
            (first == 0xed && text[i + 1] >= 0xa0) ||
            (first == 0xf0 && text[i + 1] < 0x90) ||
            (first == 0xf4 && text[i + 1] >= 0x90)) return 0;
        i += width;
    }
    return 1;
}

static char *read_text_file(const char *path, int line) {
    FILE *file = fopen(path, "rb");
    if (!file) fail_at(line, "cannot open text file for reading");
    if (fseek(file, 0, SEEK_END) != 0) {
        fclose(file);
        fail_at(line, "cannot seek text file");
    }
    long length = ftell(file);
    if (length < 0 || length > 16 * 1024 * 1024) {
        fclose(file);
        fail_at(line, "text file exceeds the 16 MiB limit");
    }
    rewind(file);
    char *text = (char *)malloc((size_t)length + 1);
    if (!text) {
        fclose(file);
        fail_at(line, "out of memory");
    }
    size_t read_count = fread(text, 1, (size_t)length, file);
    int close_status = fclose(file);
    if (read_count != (size_t)length || close_status != 0) {
        free(text);
        fail_at(line, "cannot read text file");
    }
    if (memchr(text, '\0', read_count)) {
        free(text);
        fail_at(line, "text file contains an unsupported NUL byte");
    }
    if (!valid_utf8((const unsigned char *)text, read_count)) {
        free(text);
        fail_at(line, "text file is not valid UTF-8");
    }
    text[read_count] = '\0';
    return text;
}

static char *read_text_file_owned(char *path, int line) {
    char *text = read_text_file(path, line);
    free(path);
    return text;
}

static size_t write_text_file_owned(char *path, char *text, int line) {
    FILE *file = fopen(path, "wb");
    if (!file) {
        free(path);
        free(text);
        fail_at(line, "cannot open text file for writing");
    }
    size_t length = strlen(text);
    size_t written = fwrite(text, 1, length, file);
    int close_status = fclose(file);
    size_t characters = unicode_strlen(text);
    free(path);
    free(text);
    if (written != length || close_status != 0) {
        fail_at(line, "cannot write text file");
    }
    return characters;
}

static char *concat_texts(char **parts, size_t count, int line) {
    size_t total = 0;
    for (size_t i = 0; i < count; ++i) {
        size_t length = strlen(parts[i]);
        if (length > SIZE_MAX - total - 1) fail_at(line, "string is too long");
        total += length;
    }
    char *joined = (char *)malloc(total + 1);
    if (!joined) fail_at(line, "out of memory");
    char *cursor = joined;
    for (size_t i = 0; i < count; ++i) {
        size_t length = strlen(parts[i]);
        memcpy(cursor, parts[i], length);
        cursor += length;
        free(parts[i]);
    }
    *cursor = '\0';
    return joined;
}
'''

_ATOM_RUNTIME = r'''
#if defined(_WIN32)
#include <windows.h>
typedef CRITICAL_SECTION cosmic_mutex;
typedef HANDLE cosmic_thread;
#define COSMIC_THREAD_OK 0
static void cosmic_mutex_init(cosmic_mutex *m) { InitializeCriticalSection(m); }
static void cosmic_mutex_destroy(cosmic_mutex *m) { DeleteCriticalSection(m); }
static void cosmic_mutex_lock(cosmic_mutex *m) { EnterCriticalSection(m); }
static void cosmic_mutex_unlock(cosmic_mutex *m) { LeaveCriticalSection(m); }
static int cosmic_mutex_trylock(cosmic_mutex *m) { return TryEnterCriticalSection(m) ? 1 : 0; }
static int cosmic_thread_create(cosmic_thread *t, unsigned (__stdcall *fn)(void *), void *arg) {
    *t = CreateThread(NULL, 0, (LPTHREAD_START_ROUTINE)fn, arg, 0, NULL);
    return *t ? 0 : -1;
}
static void cosmic_thread_join(cosmic_thread t) {
    if (!t) return;
    WaitForSingleObject(t, INFINITE);
    CloseHandle(t);
}
static void cosmic_thread_sleep(unsigned ms) { Sleep(ms); }
#else
#include <pthread.h>
typedef pthread_mutex_t cosmic_mutex;
typedef pthread_t cosmic_thread;
#define COSMIC_THREAD_OK 0
static void cosmic_mutex_init(cosmic_mutex *m) {
    pthread_mutexattr_t attr;
    pthread_mutexattr_init(&attr);
    pthread_mutexattr_settype(&attr, PTHREAD_MUTEX_RECURSIVE);
    pthread_mutex_init(m, &attr);
    pthread_mutexattr_destroy(&attr);
}
static void cosmic_mutex_destroy(cosmic_mutex *m) { pthread_mutex_destroy(m); }
static void cosmic_mutex_lock(cosmic_mutex *m) { pthread_mutex_lock(m); }
static void cosmic_mutex_unlock(cosmic_mutex *m) { pthread_mutex_unlock(m); }
static int cosmic_mutex_trylock(cosmic_mutex *m) { return pthread_mutex_trylock(m) == 0; }
static int cosmic_thread_create(cosmic_thread *t, void *(*fn)(void *), void *arg) {
    return pthread_create(t, NULL, fn, arg);
}
static void cosmic_thread_join(cosmic_thread t) { pthread_join(t, NULL); }
static void cosmic_thread_sleep(unsigned ms) { usleep((useconds_t)ms * 1000); }
#endif

enum { ATOM_IDLE = 0, ATOM_RUNNING = 1, ATOM_OK = 2, ATOM_STOP = 3, ATOM_ERROR = 4, ATOM_STOPPED = 5 };

typedef struct AtomBlock AtomBlock;
struct AtomBlock {
    const char *name;
    cosmic_mutex lock;
    cosmic_mutex state_lock;
    cosmic_thread thread;
    int state;
    int has_thread;
    volatile int stop;
    int has_context;
    int shared;
    Context *context;
    void (*runner)(Context *, int, Result *);
    int parent;
};

static AtomBlock cosmic_atoms[BLOCK_COUNT];
static cosmic_mutex cosmic_atom_lock;
static int cosmic_atom_lock_ready = 0;
static cosmic_mutex cosmic_output_lock;
static int cosmic_output_lock_ready = 0;

static void cosmic_atom_init(void) {
    static int initialized = 0;
    if (initialized) return;
    initialized = 1;
    cosmic_mutex_init(&cosmic_atom_lock);
    cosmic_atom_lock_ready = 1;
    cosmic_mutex_init(&cosmic_output_lock);
    cosmic_output_lock_ready = 1;
    for (int i = 0; i < BLOCK_COUNT; ++i) {
        cosmic_mutex_init(&cosmic_atoms[i].lock);
        cosmic_mutex_init(&cosmic_atoms[i].state_lock);
    }
}

enum {
    ATOM_STATUS_OK = 0,
    ATOM_STATUS_STOP = 1,
    ATOM_STATUS_ERROR = -1,
    ATOM_STATUS_IDLE = 2,
    ATOM_STATUS_RUNNING = 3,
    ATOM_STATUS_STOPPED = 4
};

static int cosmic_atom_status(int index);

static int cosmic_atom_dog(int index) {
    switch (cosmic_atom_status(index)) {
        case ATOM_OK: return ATOM_STATUS_OK;
        case ATOM_STOP: return ATOM_STATUS_STOP;
        case ATOM_ERROR: return ATOM_STATUS_ERROR;
        case ATOM_RUNNING: return ATOM_STATUS_RUNNING;
        case ATOM_STOPPED: return ATOM_STATUS_STOPPED;
        default: return ATOM_STATUS_IDLE;
    }
}

static const char *cosmic_atom_state_name(int state) {
    switch (state) {
        case ATOM_RUNNING: return "running";
        case ATOM_OK: return "ok";
        case ATOM_STOP: return "stop";
        case ATOM_ERROR: return "error";
        case ATOM_STOPPED: return "stopped";
        default: return "idle";
    }
}

static int cosmic_atom_status(int index) {
    cosmic_atom_init();
    cosmic_mutex_lock(&cosmic_atom_lock);
    int state = cosmic_atoms[index].state;
    cosmic_mutex_unlock(&cosmic_atom_lock);
    return state;
}

static volatile int *cosmic_current_stop = NULL;

static void cosmic_atom_release_context(int index) {
    cosmic_atom_init();
    cosmic_mutex_lock(&cosmic_atom_lock);
    AtomBlock *block = &cosmic_atoms[index];
    if (block->has_context && block->context) {
        free_context(block->context);
        free(block->context);
        block->context = NULL;
        block->has_context = 0;
    }
    cosmic_mutex_unlock(&cosmic_atom_lock);
}

static void cosmic_atom_release_all(void) {
    cosmic_atom_init();
    for (int i = 0; i < BLOCK_COUNT; ++i) {
        AtomBlock *block = &cosmic_atoms[i];
        if (block->has_thread) {
            cosmic_thread_join(block->thread);
            block->has_thread = 0;
        }
        if (block->has_context && block->context && !block->shared) {
            free_context(block->context);
            free(block->context);
        }
        block->context = NULL;
        block->has_context = 0;
    }
}
'''

_DLL_RUNTIME = r'''
typedef struct DllEntry {
    const char *name;
    void *handle;
    int loaded;
    struct DllEntry *next;
} DllEntry;
static DllEntry *cosmic_dlls;

static DllEntry *cosmic_dll_entry(const char *name) {
    for (DllEntry *entry = cosmic_dlls; entry; entry = entry->next) {
        if (_stricmp(entry->name, name) == 0) return entry;
    }
    return NULL;
}

static const char *cosmic_dll_status(const char *name) {
    DllEntry *entry = cosmic_dll_entry(name);
    if (!entry) return "missing";
    return entry->loaded ? "loaded" : "unloaded";
}

#ifdef _WIN32
static HMODULE cosmic_load_library_owned(const char *name) {
    return LoadLibraryA(name);
}
#else
static void *cosmic_load_library_owned(const char *name) {
    return dlopen(name, RTLD_NOW);
}
#endif

static int cosmic_dll_load(const char *name, int line) {
    DllEntry *existing = cosmic_dll_entry(name);
    if (existing && existing->loaded) return 0;
    void *handle = NULL;
#ifdef _WIN32
    handle = (void *)cosmic_load_library_owned(name);
    if (!handle && !strchr(name, '.')) {
        char *with_extension = (char *)malloc(strlen(name) + 5);
        if (with_extension) {
            sprintf(with_extension, "%s.dll", name);
            handle = (void *)cosmic_load_library_owned(with_extension);
            free(with_extension);
        }
    }
#else
    handle = cosmic_load_library_owned(name);
    if (!handle && strncmp(name, "lib", 3) != 0) {
        char *with_prefix = (char *)malloc(strlen(name) + 9);
        if (with_prefix) {
            sprintf(with_prefix, "lib%s.so", name);
            handle = cosmic_load_library_owned(with_prefix);
            free(with_prefix);
        }
    }
#endif
    if (!handle) return 0;
    if (!existing) {
        existing = (DllEntry *)malloc(sizeof(DllEntry));
        if (!existing) fail_at(line, "out of memory");
        existing->name = copy_text(name);
        existing->handle = NULL;
        existing->loaded = 0;
        existing->next = cosmic_dlls;
        cosmic_dlls = existing;
    }
    existing->handle = handle;
    existing->loaded = 1;
    return 1;
}

static int cosmic_dll_unload(const char *name) {
    DllEntry *entry = cosmic_dll_entry(name);
    if (!entry || !entry->loaded) return 0;
#ifdef _WIN32
    FreeLibrary((HMODULE)entry->handle);
#else
    dlclose(entry->handle);
#endif
    entry->handle = NULL;
    entry->loaded = 0;
    return 1;
}

static void cosmic_dll_release_all(void) {
    while (cosmic_dlls) {
        DllEntry *entry = cosmic_dlls;
        cosmic_dlls = entry->next;
        if (entry->loaded) {
#ifdef _WIN32
            FreeLibrary((HMODULE)entry->handle);
#else
            dlclose(entry->handle);
#endif
        }
        free((void *)entry->name);
        free(entry);
    }
}

static void *cosmic_dll_lookup(void *handle, const char *function) {
#ifdef _WIN32
    FARPROC proc = GetProcAddress((HMODULE)handle, function);
    return (void *)(uintptr_t)proc;
#else
    return dlsym(handle, function);
#endif
}

static void *cosmic_dll_symbol(const char *library, const char *function) {
    DllEntry *entry = cosmic_dll_entry(library);
    if (!entry || !entry->loaded) return NULL;
    void *symbol = cosmic_dll_lookup(entry->handle, function);
    if (symbol) return symbol;
    size_t length = strlen(function);
    if (!length) return NULL;
    char *variant = (char *)malloc(length + 1);
    if (!variant) return NULL;
    memcpy(variant, function, length + 1);
    if (variant[0] >= 'a' && variant[0] <= 'z') {
        variant[0] = (char)(variant[0] - 'a' + 'A');
        symbol = cosmic_dll_lookup(entry->handle, variant);
    }
    free(variant);
    return symbol;
}
'''

_BLAS_RUNTIME = r'''
extern double ddot_(const int *, const double *, const int *, const double *, const int *);
static double cosmic_blas_dot(const double *left, const double *right, size_t count, int line) {
    if (count > INT_MAX) fail_at(line, "BLAS vector length exceeds INT_MAX");
    int n = (int)count, increment = 1;
    return ddot_(&n, left, &increment, right, &increment);
}
'''

_LAPACK_RUNTIME = r'''
extern void dgetrf_(const int *, const int *, double *, const int *, int *, int *);
static double cosmic_lapack_determinant2(double a, double b, double c, double d, int line) {
    int size = 2, leading_dimension = 2, pivots[2] = {0, 0}, info = 0;
    double values[4] = {a, c, b, d};
    dgetrf_(&size, &size, values, &leading_dimension, pivots, &info);
    if (info < 0) fail_at(line, "LAPACK dgetrf rejected an argument");
    if (info > 0) return 0.0;
    double determinant = values[0] * values[3];
    if (pivots[0] != 1) determinant = -determinant;
    return determinant;
}
'''


def _dog_status_constant(status: str) -> str:
    normalized = "ok" if status in {"pass", "ok"} else status
    if normalized in {"idle", "running", "stopped"}:
        return f"ATOM_STATUS_{normalized.upper()}"
    return f"STATUS_{normalized.upper()}"


def _c_string(value: str) -> str:
    encoded = value.encode("utf-8")
    result = ['"']
    for byte in encoded:
        if byte == 0x22:
            result.append(r'\"')
        elif byte == 0x5C:
            result.append(r"\\")
        elif byte == 0x0A:
            result.append(r"\n")
        elif byte == 0x0D:
            result.append(r"\r")
        elif byte == 0x09:
            result.append(r"\t")
        elif 0x20 <= byte < 0x7F:
            result.append(chr(byte))
        else:
            result.append(f"\\{byte:03o}")
    result.append('"')
    return "".join(result)


def _identifier(name: str) -> str:
    return "var_" + re.sub(r"\W", "_", name)


def _is_string_expression(expression: str) -> bool:
    transformed, _variables = transform_braced_expression(expression)
    transformed = re.sub(r"tape\s*\(\s*([A-Za-z_]\w*)\s*\)", r"tape_\1", transformed, flags=re.I)
    try:
        tree = ast.parse(transformed.strip(), mode="eval")
    except SyntaxError:
        return False
    return (
        isinstance(tree.body, ast.Call)
        and isinstance(tree.body.func, ast.Name)
        and tree.body.func.id.lower() in {"chr", "char_at", "read_text", "hex"}
    )


def _is_array_value_expression(expression: str, array_names: set[str]) -> bool:
    transformed, _variables = transform_braced_expression(expression)
    try:
        body = ast.parse(transformed.strip(), mode="eval").body
    except SyntaxError:
        return False
    if isinstance(body, ast.List):
        return True
    if isinstance(body, ast.Name):
        return body.id in array_names
    if isinstance(body, ast.Call) and isinstance(body.func, ast.Name):
        function_name = body.func.id.lower()
        if function_name in {"array_set", "array_push"}:
            return True
        if function_name == "choose" and len(body.args) == 3:
            return any(
                _is_array_ast_node(argument, array_names)
                for argument in body.args[1:]
            )
    return False


def _is_array_ast_node(node: ast.expr, array_names: set[str]) -> bool:
    if isinstance(node, ast.List):
        return True
    if isinstance(node, ast.Name):
        return node.id in array_names
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        function_name = node.func.id.lower()
        if function_name in {"array_set", "array_push"}:
            return True
        if function_name == "choose" and len(node.args) == 3:
            return any(
                _is_array_ast_node(argument, array_names)
                for argument in node.args[1:]
            )
    return False


def _array_variable_references(node: ast.expr) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id} if node.id.startswith("var_") else set()
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        name = node.func.id.lower()
        if name in {"array_set", "array_push"} and node.args:
            return _array_variable_references(node.args[0])
        if name == "choose" and len(node.args) == 3:
            return set().union(
                *(_array_variable_references(argument) for argument in node.args[1:])
            )
    return set()


def _is_bigint_result_node(
    node: ast.expr, parameters: set[str], user_functions: set[str]
) -> bool:
    if isinstance(node, ast.Constant):
        return type(node.value) is int
    if isinstance(node, ast.Name):
        return (
            node.id in parameters
            or node.id.startswith(("var_", "tape_"))
        )
    if isinstance(node, ast.UnaryOp):
        return isinstance(node.op, (ast.UAdd, ast.USub, ast.Invert)) and _is_bigint_result_node(
            node.operand, parameters, user_functions
        )
    if isinstance(node, ast.BinOp):
        return (
            isinstance(
                node.op,
                (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Mod, ast.Pow,
                 ast.BitAnd, ast.BitOr, ast.BitXor, ast.LShift, ast.RShift),
            )
            and _is_bigint_result_node(node.left, parameters, user_functions)
            and _is_bigint_result_node(node.right, parameters, user_functions)
        )
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        name = node.func.id.lower()
        if name == "unhex":
            return len(node.args) == 1 and not node.keywords
        if name == "hex":
            # hex() yields text, which bigint mode may print or unhex back.
            return len(node.args) in {1, 2} and not node.keywords
        if name in {"ord", "strlen"}:
            return len(node.args) == 1 and not node.keywords
        if name == "rag_range":
            return len(node.args) == 2 and not node.keywords
        if name == "array_len":
            return len(node.args) == 1 and not node.keywords
        if name == "abs":
            return len(node.args) == 1 and not node.keywords and _is_bigint_result_node(
                node.args[0], parameters, user_functions
            )
        if name == "choose":
            return (
                len(node.args) == 3
                and not node.keywords
                and all(
                    _is_bigint_result_node(argument, parameters, user_functions)
                    for argument in node.args[1:]
                )
            )
        return name in user_functions
    return False


def _c_expression(
    expression: str,
    context: str,
    line: int,
    variable_indices: dict[str, int],
    tape_indices: dict[str, int],
    *,
    integer_mode: bool = False,
    bigint_mode: bool = False,
    complex_mode: bool = False,
    user_functions: dict[str, int] | None = None,
    parameters: set[str] | None = None,
    array_indices: dict[str, int] | None = None,
    array_variable_indices: dict[str, int] | None = None,
    array_mode: bool = False,
    bigint_function_suffix: str = "",
    uint32_mode: bool = False,
    matrix_result: bool = False,
) -> str:
    array_indices = array_indices or {}
    array_variable_indices = array_variable_indices or {}
    array_names = set(array_indices) | set(array_variable_indices)
    try:
        transformed, _variables = transform_braced_expression(expression)
    except ValueError as error:
        raise ParseError(line, f"invalid expression: {error}") from error
    transformed = re.sub(r"tape\s*\(\s*([A-Za-z_]\w*)\s*\)", r"tape_\1", transformed, flags=re.I)
    try:
        transformed = normalize_rag_calls(transformed)
    except ValueError as error:
        raise ParseError(line, f"invalid expression: {error}") from error
    try:
        tree = ast.parse(transformed.strip(), mode="eval")
    except SyntaxError as error:
        raise ParseError(line, f"invalid expression: {error.msg}") from error

    def is_integer_node(node: ast.expr) -> bool:
        if isinstance(node, ast.Constant):
            return type(node.value) is int
        if isinstance(node, ast.Name):
            return node.id.startswith("var_") or node.id.startswith("tape_")
        if isinstance(node, ast.UnaryOp):
            if _is_array_ast_node(node.operand, array_names):
                raise ValueError("array values must be passed to an array function")
            return isinstance(node.op, (ast.UAdd, ast.USub)) and is_integer_node(node.operand)
        if isinstance(node, ast.BinOp):
            return (
                isinstance(
                    node.op,
                    (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv,
                     ast.Mod, ast.Pow, ast.BitAnd, ast.BitOr, ast.BitXor),
                )
                and is_integer_node(node.left)
                and is_integer_node(node.right)
            )
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Invert):
            return is_integer_node(node.operand)
        return False

    def convert_array(node: ast.expr) -> str:
        scalar_type = "double complex" if complex_mode else "double"
        literal_constructor = "cosmic_complex_array" if complex_mode else "cosmic_array"
        if isinstance(node, ast.List):
            if len(node.elts) > 1_000_000:
                raise ValueError("array literals may contain at most one million elements")
            if any(_is_array_ast_node(value, array_names) for value in node.elts):
                raise ValueError("nested arrays are not supported")
            values = ", ".join(convert(value) for value in node.elts) or "0.0"
            return f"{literal_constructor}(({scalar_type}[]){{{values}}}, {len(node.elts)})"
        if isinstance(node, ast.Name):
            if node.id in array_variable_indices:
                getter = (
                    "cosmic_get_variable_complex_array"
                    if complex_mode else "cosmic_get_variable_array"
                )
                return f"{getter}({context}, {array_variable_indices[node.id]}, {line})"
            if node.id not in array_indices:
                raise ValueError(f"unknown array '{node.id}'")
            getter = "cosmic_get_complex_array" if complex_mode else "cosmic_get_array"
            return f"{getter}({context}, {array_indices[node.id]}, {line})"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            function_name = node.func.id.lower()
            if function_name == "solve_linear":
                if node.keywords or len(node.args) != 2:
                    raise ValueError("solve_linear expects a square matrix and a right-hand side")
                if complex_mode:
                    raise ValueError("solve_linear requires a real-valued math mode")
                return (
                    f"cosmic_solve_linear({convert_matrix(node.args[0])}, "
                    f"{convert_array(node.args[1])}, {line})"
                )
            if function_name == "array_set":
                if node.keywords or len(node.args) != 3:
                    raise ValueError("array_set expects an array, an index, and a value")
                if any(
                    _is_array_ast_node(value, array_names)
                    for value in node.args[1:]
                ):
                    raise ValueError("array_set index and value must be numeric")
                if isinstance(node.args[0], ast.Name) and (
                    node.args[0].id in array_indices or node.args[0].id in array_variable_indices
                ):
                    if node.args[0].id in array_variable_indices:
                        function = (
                            "cosmic_complex_array_set_dynamic_variable"
                            if complex_mode else "cosmic_array_set_dynamic_variable"
                        )
                        source_index = array_variable_indices[node.args[0].id]
                    else:
                        function = (
                            "cosmic_complex_array_set_named"
                            if complex_mode else "cosmic_array_set_variable"
                        )
                        source_index = array_indices[node.args[0].id]
                    return (
                        f"{function}({context}, {source_index}, "
                        f"(double)({convert(node.args[1])}), "
                        f"({scalar_type})({convert(node.args[2])}), {line})"
                    )
                function = "cosmic_complex_array_set" if complex_mode else "cosmic_array_set"
                return (
                    f"{function}({convert_array(node.args[0])}, "
                    f"(double)({convert(node.args[1])}), "
                    f"({scalar_type})({convert(node.args[2])}), {line})"
                )
            if function_name == "array_push":
                if node.keywords or len(node.args) != 2:
                    raise ValueError("array_push expects an array and a value")
                if _is_array_ast_node(node.args[1], array_names):
                    raise ValueError("array_push value must be numeric")
                if isinstance(node.args[0], ast.Name) and (
                    node.args[0].id in array_indices or node.args[0].id in array_variable_indices
                ):
                    if node.args[0].id in array_variable_indices:
                        function = (
                            "cosmic_complex_array_push_dynamic_variable"
                            if complex_mode else "cosmic_array_push_dynamic_variable"
                        )
                        source_index = array_variable_indices[node.args[0].id]
                    else:
                        function = (
                            "cosmic_complex_array_push_named"
                            if complex_mode else "cosmic_array_push_variable"
                        )
                        source_index = array_indices[node.args[0].id]
                    return (
                        f"{function}({context}, {source_index}, "
                        f"({scalar_type})({convert(node.args[1])}), {line})"
                    )
                function = "cosmic_complex_array_push" if complex_mode else "cosmic_array_push"
                return (
                    f"{function}({convert_array(node.args[0])}, "
                    f"({scalar_type})({convert(node.args[1])}), {line})"
                )
        raise ValueError("array argument must be an array literal, array variable, or array_set/array_push result")

    def convert_matrix(node: ast.expr) -> str:
        if complex_mode:
            raise ValueError("matrix functions require a real-valued math mode")
        if isinstance(node, ast.List):
            if not node.elts:
                raise ValueError("matrix must have at least one row and one column")
            if all(isinstance(element, ast.List) for element in node.elts):
                widths = {len(element.elts) for element in node.elts}
                if len(widths) != 1:
                    raise ValueError("matrix rows must all have the same length")
                columns = widths.pop()
                if columns == 0:
                    raise ValueError("matrix rows must not be empty")
                values = ", ".join(
                    convert(value)
                    for element in node.elts
                    for value in element.elts
                ) or "0.0"
                return (
                    f"cosmic_matrix((const double[]){{{values}}}, {len(node.elts)}, "
                    f"{columns}, {line})"
                )
            if any(isinstance(element, ast.List) for element in node.elts):
                raise ValueError("matrix rows must all be lists")
            count = len(node.elts)
            side = math.isqrt(count)
            if side * side != count:
                raise ValueError("matrix must be a list of rows or a flat square list")
            values = ", ".join(convert(value) for value in node.elts) or "0.0"
            return f"cosmic_matrix((const double[]){{{values}}}, {side}, {side}, {line})"
        if isinstance(node, ast.Name):
            if node.id in array_variable_indices:
                return (
                    f"cosmic_get_variable_matrix({context}, "
                    f"{array_variable_indices[node.id]}, {line})"
                )
            if node.id in array_indices:
                return f"cosmic_get_matrix({context}, {array_indices[node.id]}, {line})"
            raise ValueError(f"unknown matrix '{node.id}'")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            function_name = node.func.id.lower()
            if function_name in {
                "matrix_mul", "matrix_transpose", "matrix2_inverse", "matrix3_inverse",
            }:
                return convert_matrix_call(function_name, node)
        raise ValueError(
            "matrix argument must be a nested list literal, a matrix variable, or a matrix result"
        )

    def convert_matrix_call(function_name: str, node: ast.Call) -> str:
        if node.keywords:
            raise ValueError(f"{function_name} does not accept keyword arguments")
        if function_name == "matrix_rows":
            if len(node.args) != 1:
                raise ValueError("matrix_rows expects one matrix")
            return f"cosmic_matrix_rows({convert_matrix(node.args[0])})"
        if function_name == "matrix_cols":
            if len(node.args) != 1:
                raise ValueError("matrix_cols expects one matrix")
            return f"cosmic_matrix_columns({convert_matrix(node.args[0])})"
        if function_name == "matrix_at":
            if len(node.args) != 3:
                raise ValueError("matrix_at expects a matrix, a row, and a column")
            return (
                f"cosmic_matrix_at({convert_matrix(node.args[0])}, "
                f"(double)({convert(node.args[1])}), "
                f"(double)({convert(node.args[2])}), {line})"
            )
        if function_name == "matrix_mul":
            if len(node.args) != 2:
                raise ValueError("matrix_mul expects two matrices")
            return (
                f"cosmic_matrix_mul({convert_matrix(node.args[0])}, "
                f"{convert_matrix(node.args[1])}, {line})"
            )
        if function_name == "matrix_transpose":
            if len(node.args) != 1:
                raise ValueError("matrix_transpose expects one matrix")
            return f"cosmic_matrix_transpose({convert_matrix(node.args[0])}, {line})"
        order = 2 if function_name == "matrix2_inverse" else 3
        if len(node.args) != 1:
            raise ValueError(f"{function_name} expects one matrix")
        return (
            f"cosmic_matrix_inverse({convert_matrix(node.args[0])}, {order}, {line})"
        )

    def convert(node: ast.expr) -> str:
        if not isinstance(node, ast.List) and _is_array_ast_node(
            node, array_names
        ):
            raise ValueError("array values must be passed to an array function")
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            if bigint_mode and type(node.value) is int:
                return f"bigint_parse({_c_string(str(node.value))}, {line})"
            if integer_mode and type(node.value) is int:
                if not -(2**63) <= node.value <= 2**63 - 1:
                    raise ValueError("integer literal is outside int64 range")
                return f"((int64_t)({node.value}))"
            return f"((double)({node.value!r}))"
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return _c_string(node.value)
        if isinstance(node, ast.Name):
            if parameters and node.id in parameters:
                return "param_" + node.id
            if node.id in array_indices:
                raise ValueError(f"array '{node.id}' must be passed to an array function")
            constants = {"pi": "acos(-1.0)", "e": "exp(1.0)", "tau": "(2.0 * acos(-1.0))"}
            if node.id.lower() in constants:
                return constants[node.id.lower()]
            if node.id.startswith("var_"):
                name = node.id[4:]
                if name not in variable_indices:
                    raise ValueError(f"unknown variable '{name}'")
                index = variable_indices[name]
                function = "integer_variable" if integer_mode else "numeric_variable"
                if complex_mode:
                    function = "complex_variable"
                if bigint_mode:
                    function = "bigint_variable"
                return f"{function}({context}, {index}, {line})"
            if node.id.startswith("tape_"):
                name = node.id[5:]
                if name not in tape_indices:
                    raise ValueError(f"unknown tape '{name}'")
                if bigint_mode:
                    return f"bigint_from_int64((int64_t)tape_read_cell({tape_indices[name]}, {line}), {line})"
                return f"((double)tape_read_cell({tape_indices[name]}, {line}))"
            raise ValueError(f"unknown value '{node.id}'")
        if isinstance(node, ast.BinOp):
            if _is_array_ast_node(node.left, array_names) or _is_array_ast_node(
                node.right, array_names
            ):
                raise ValueError("array values must be passed to an array function")
            if uint32_mode:
                unsigned: dict[type[ast.operator], str] = {
                    ast.Add: "u32_add",
                    ast.Sub: "u32_sub",
                    ast.Mult: "u32_mul",
                    ast.Div: "u32_div",
                    ast.FloorDiv: "u32_div",
                    ast.Mod: "u32_mod",
                    ast.Pow: "u32_pow",
                    ast.BitAnd: "u32_and",
                    ast.BitOr: "u32_or",
                    ast.BitXor: "u32_xor",
                }
                if isinstance(node.op, (ast.LShift, ast.RShift)):
                    shift_left = 1 if isinstance(node.op, ast.LShift) else 0
                    return (
                        f"u32_shift((uint32_t)({convert(node.left)}), "
                        f"(uint32_t)({convert(node.right)}), {shift_left}, {line})"
                    )
                helper = unsigned.get(type(node.op))
                if helper is None:
                    raise ValueError("unsupported uint32 operator")
                return (
                    f"{helper}((uint32_t)({convert(node.left)}), "
                    f"(uint32_t)({convert(node.right)}), {line})"
                )
            if bigint_mode:
                functions: dict[type[ast.operator], str] = {
                    ast.Add: "bigint_add",
                    ast.Sub: "bigint_subtract",
                    ast.Mult: "bigint_multiply",
                    ast.FloorDiv: "bigint_floor_divide",
                    ast.Mod: "bigint_modulo",
                    ast.Pow: "bigint_power",
                    ast.BitAnd: "bigint_and",
                    ast.BitOr: "bigint_or",
                    ast.BitXor: "bigint_xor",
                }
                if isinstance(node.op, (ast.LShift, ast.RShift)):
                    shift_left = 1 if isinstance(node.op, ast.LShift) else 0
                    return (
                        f"bigint_shift({convert(node.left)}, {convert(node.right)}, "
                        f"{shift_left}, {line})"
                    )
                function = functions.get(type(node.op))
                if function is None:
                    raise ValueError("bigint mode requires integer-only expressions")
                return f"{function}({convert(node.left)}, {convert(node.right)}, {line})"
            if integer_mode and not uint32_mode and is_integer_node(node):
                functions: dict[type[ast.operator], str] = {
                    ast.Add: "checked_add",
                    ast.Sub: "checked_sub",
                    ast.Mult: "checked_mul",
                    ast.Div: "checked_div",
                    ast.FloorDiv: "checked_floor_div",
                    ast.Mod: "checked_mod",
                    ast.Pow: "checked_pow",
                    ast.BitAnd: "checked_bit_and",
                    ast.BitOr: "checked_bit_or",
                    ast.BitXor: "checked_bit_xor",
                }
                function = functions.get(type(node.op))
                if function is None:
                    raise ValueError("unsupported integer operator")
                return (
                    f"{function}({convert(node.left)}, {convert(node.right)}, {line})"
                )
            if isinstance(node.op, (ast.BitAnd, ast.BitOr, ast.BitXor,
                                    ast.LShift, ast.RShift)):
                if not integer_mode and not bigint_mode and not uint32_mode:
                    raise ValueError(
                        "bitwise operators require an integer math mode"
                    )
                bit_operators: dict[type[ast.operator], str] = {
                    ast.BitAnd: "&", ast.BitOr: "|", ast.BitXor: "^",
                    ast.LShift: "<<", ast.RShift: ">>",
                }
                left_text, right_text = convert(node.left), convert(node.right)
                if isinstance(node.op, (ast.LShift, ast.RShift)):
                    shift_left = 1 if isinstance(node.op, ast.LShift) else 0
                    return (
                        f"checked_shift((int64_t)({left_text}), "
                        f"(int64_t)({right_text}), {shift_left}, {line})"
                    )
                return (
                    f"(({left_text}) {bit_operators[type(node.op)]} ({right_text}))"
                )
            operators: dict[type[ast.operator], str] = {
                ast.Add: "+",
                ast.Sub: "-",
                ast.Mult: "*",
                ast.Div: "/",
            }
            if isinstance(node.op, ast.FloorDiv):
                if complex_mode:
                    raise ValueError("floor division is not defined for complex values")
                left, right = convert(node.left), convert(node.right)
                return f"floor(({left}) / ({right}))"
            if isinstance(node.op, ast.Mod):
                if complex_mode:
                    raise ValueError("modulo is not defined for complex values")
                left, right = convert(node.left), convert(node.right)
                return f"fmod(({left}), ({right}))"
            if isinstance(node.op, ast.Pow):
                left, right = convert(node.left), convert(node.right)
                return f"{'cpow' if complex_mode else 'pow'}(({left}), ({right}))"
            operator_text = operators.get(type(node.op))
            if operator_text is None:
                raise ValueError("unsupported operator")
            left, right = convert(node.left), convert(node.right)
            return f"(({left}) {operator_text} ({right}))"
        if isinstance(node, ast.List):
            if len(node.elts) > 1_000_000:
                raise ValueError("array literals may contain at most one million elements")
            element_type = "double complex" if complex_mode else "double"
            elements = ", ".join(convert(value) for value in node.elts) or "0.0"
            return f"(({element_type}[]){{{elements}}})"
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.UAdd):
                return f"(+({convert(node.operand)}))"
            if isinstance(node.op, ast.USub):
                if bigint_mode:
                    return f"bigint_negate({convert(node.operand)})"
                return f"(-({convert(node.operand)}))"
            if isinstance(node.op, ast.Invert):
                if not integer_mode and not bigint_mode and not uint32_mode:
                    raise ValueError(
                        "bitwise operators require an integer math mode"
                    )
                if bigint_mode:
                    return f"bigint_invert({convert(node.operand)}, {line})"
                if uint32_mode:
                    return f"(~(uint32_t)({convert(node.operand)}))"
                return f"(~({convert(node.operand)}))"
            if isinstance(node.op, ast.Not):
                return f"(!({convert(node.operand)}))"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            function_name = node.func.id.lower()
            if bigint_mode and function_name == "abs":
                if node.keywords or len(node.args) != 1:
                    raise ValueError("abs expects one argument")
                return f"bigint_absolute({convert(node.args[0])})"
            array_consumers = {
                "array_len", "array_set", "array_push", "array_sum", "array_at",
                "bytes_len", "bytes_get",
                "mean", "std", "min", "max", "dot", "blas_dot",
                "matrix2_det", "matrix2_mul_entry", "lapack_determinant2",
                "matrix_rows", "matrix_cols", "matrix_at", "matrix_mul",
                "matrix_transpose", "matrix2_inverse", "matrix3_inverse",
                "solve_linear", "median", "variance", "correlation",
                "percentile", "trapz", "simpson",
            }
            if function_name in {
                "matrix_rows", "matrix_cols", "matrix_at", "matrix_mul",
                "matrix_transpose", "matrix2_inverse", "matrix3_inverse",
            }:
                return convert_matrix_call(function_name, node)
            if function_name not in array_consumers and any(
                _is_array_ast_node(argument, array_names)
                for argument in node.args
            ):
                raise ValueError("array values must be passed to an array function")
            if (
                not complex_mode
                and function_name in {"abs", "cabs", "real", "imag"}
                and any(
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                    and child.func.id.lower() == "complex"
                    for argument in node.args
                    for child in ast.walk(argument)
                )
            ):
                return _c_expression(
                    ast.unparse(node),
                    context,
                    line,
                    variable_indices,
                    tape_indices,
                    user_functions=user_functions,
                    parameters=parameters,
                    array_indices=array_indices,
                    array_variable_indices=array_variable_indices, uint32_mode=uint32_mode,
                    complex_mode=True,
                )
            if function_name == "complex" and not complex_mode:
                raise ValueError("complex() requires complex math mode")
            if function_name == "blas_dot":
                if complex_mode:
                    raise ValueError("blas_dot currently supports real-valued arrays only")
                if node.keywords or len(node.args) != 2 or not all(
                    isinstance(argument, ast.List) for argument in node.args
                ):
                    raise ValueError("blas_dot expects two array literals")
                left, right = node.args
                if len(left.elts) != len(right.elts):
                    raise ValueError("blas_dot arrays must have equal lengths")
                return (
                    f"cosmic_blas_dot({convert(left)}, {convert(right)}, "
                    f"{len(left.elts)}, {line})"
                )
            if function_name == "lapack_determinant2":
                if complex_mode:
                    raise ValueError("lapack_determinant2 currently supports real-valued matrices only")
                if node.keywords or len(node.args) != 1 or not isinstance(node.args[0], ast.List):
                    raise ValueError("lapack_determinant2 expects a 2 by 2 matrix literal")
                matrix = node.args[0]
                if len(matrix.elts) != 2 or any(
                    not isinstance(row, ast.List) or len(row.elts) != 2
                    for row in matrix.elts
                ):
                    raise ValueError("lapack_determinant2 requires a 2 by 2 matrix literal")
                a, b = matrix.elts[0].elts
                c, d = matrix.elts[1].elts
                return (
                    f"cosmic_lapack_determinant2({convert(a)}, {convert(b)}, "
                    f"{convert(c)}, {convert(d)}, {line})"
                )
            if function_name in {"array_len", "mean", "std", "min", "max"}:
                if node.keywords or len(node.args) != 1:
                    raise ValueError(f"{function_name} expects one array")
                if complex_mode:
                    if function_name != "array_len":
                        raise ValueError(f"{function_name} currently supports real-valued arrays only")
                    return f"cosmic_complex_array_length({convert_array(node.args[0])})"
                array = convert_array(node.args[0])
                if function_name == "array_len":
                    if bigint_mode:
                        return (
                            f"bigint_from_int64((int64_t)cosmic_array_length({array}), {line})"
                        )
                    return f"cosmic_array_length({array})"
                return f"cosmic_array_{function_name}({array}, {line})"
            if function_name in {"array_set", "array_push"}:
                return convert_array(node)
            if function_name in {"array_sum", "array_at"}:
                expected_arity = 1 if function_name == "array_sum" else 2
                if node.keywords or len(node.args) != expected_arity:
                    raise ValueError(f"{function_name} expects an array literal")
                array = node.args[0]
                if function_name == "array_at" and _is_array_ast_node(
                    node.args[1], array_names
                ):
                    raise ValueError("array_at index must be numeric")
                if complex_mode:
                    converted = convert_array(array)
                    sum_function = "cosmic_complex_array_sum"
                    at_function = "cosmic_complex_array_at"
                    length = None
                else:
                    converted = convert_array(array)
                    sum_function = "cosmic_array_sum"
                    at_function = "cosmic_array_at"
                    length = None
                if function_name == "array_sum":
                    return (
                        f"{sum_function}({converted})"
                        if complex_mode
                        else f"{sum_function}({converted})"
                    )
                return (
                    f"{at_function}({converted}, "
                    f"(double)({convert(node.args[1])}), {line})"
                    if complex_mode
                    else f"{at_function}({converted}, (double)({convert(node.args[1])}), {line})"
                )
            if function_name in {
                "median", "variance", "correlation", "percentile", "trapz", "simpson",
            }:
                if complex_mode:
                    raise ValueError(f"{function_name} currently supports real-valued arrays only")
                if function_name in {"median", "variance"}:
                    if node.keywords or len(node.args) != 1:
                        raise ValueError(f"{function_name} expects one array")
                    return f"cosmic_array_{function_name}({convert_array(node.args[0])}, {line})"
                if function_name == "correlation":
                    if node.keywords or len(node.args) != 2:
                        raise ValueError("correlation expects two arrays")
                    return (
                        f"cosmic_array_correlation({convert_array(node.args[0])}, "
                        f"{convert_array(node.args[1])}, {line})"
                    )
                if node.keywords or len(node.args) != 2:
                    raise ValueError(f"{function_name} expects an array and a number")
                if _is_array_ast_node(node.args[1], array_names):
                    raise ValueError(f"{function_name} second argument must be numeric")
                return (
                    f"cosmic_array_{function_name}({convert_array(node.args[0])}, "
                    f"(double)({convert(node.args[1])}), {line})"
                )
            if function_name == "dot":
                if node.keywords or len(node.args) != 2:
                    raise ValueError("dot expects two arrays")
                left, right = node.args
                if complex_mode:
                    return (
                        f"cosmic_complex_array_dot({convert_array(left)}, "
                        f"{convert_array(right)}, {line})"
                    )
                return (
                    f"cosmic_array_dot({convert_array(left)}, {convert_array(right)}, {line})"
                )
            if function_name == "matrix2_det":
                if node.keywords or len(node.args) != 1 or not isinstance(node.args[0], ast.List):
                    raise ValueError("matrix2_det expects a 2 by 2 matrix literal")
                matrix = node.args[0]
                if len(matrix.elts) != 2 or any(
                    not isinstance(row, ast.List) or len(row.elts) != 2
                    for row in matrix.elts
                ):
                    raise ValueError("matrix2_det requires a 2 by 2 matrix literal")
                a, b = matrix.elts[0].elts
                c, d = matrix.elts[1].elts
                return f"(({convert(a)}) * ({convert(d)}) - ({convert(b)}) * ({convert(c)}))"
            if function_name == "matrix2_mul_entry":
                if node.keywords or len(node.args) != 4 or not all(
                    isinstance(node.args[index], ast.List) for index in (0, 1)
                ):
                    raise ValueError("matrix2_mul_entry expects two matrices and row/column indices")
                matrices = node.args[:2]
                if any(
                    len(matrix.elts) != 2
                    or any(not isinstance(row, ast.List) or len(row.elts) != 2 for row in matrix.elts)
                    for matrix in matrices
                ):
                    raise ValueError("matrix2_mul_entry requires two 2 by 2 matrix literals")
                flattened = [
                    convert(element)
                    for matrix in matrices
                    for row in matrix.elts
                    for element in row.elts
                ]
                element_type = "double complex" if complex_mode else "double"
                left = "((" + element_type + "[]){" + ", ".join(flattened[:4]) + "})"
                right = "((" + element_type + "[]){" + ", ".join(flattened[4:]) + "})"
                return (
                    f"{'complex_matrix2_mul_entry' if complex_mode else 'matrix2_mul_entry'}({left}, {right}, "
                    f"(double)({convert(node.args[2])}), "
                    f"(double)({convert(node.args[3])}), {line})"
                )
            if function_name == "choose":
                if node.keywords or len(node.args) != 3:
                    raise ValueError("choose expects a condition and two values")
                if any(
                    _is_array_ast_node(argument, array_names)
                    for argument in node.args[1:]
                ):
                    raise ValueError("choose branches must be scalar values")
                return (
                    f"(({convert(node.args[0])}) ? ({convert(node.args[1])}) : "
                    f"({convert(node.args[2])}))"
                )
            if user_functions and function_name in user_functions:
                if bigint_mode and bigint_function_suffix:
                    expected_arity = user_functions[function_name]
                    if node.keywords or len(node.args) != expected_arity:
                        raise ValueError(
                            f"{function_name} expects {expected_arity} argument(s)"
                        )
                    arguments = ", ".join(convert(argument) for argument in node.args)
                    return f"user_{function_name}{bigint_function_suffix}({arguments})"
                expected_arity = user_functions[function_name]
                if node.keywords or len(node.args) != expected_arity:
                    raise ValueError(
                        f"{function_name} expects {expected_arity} argument(s)"
                    )
                arguments = ", ".join(convert(argument) for argument in node.args)
                return f"user_{function_name}({arguments})"
            if function_name in {"read_text", "write_text"}:
                expected_arity = 1 if function_name == "read_text" else 2
                if node.keywords or len(node.args) != expected_arity:
                    raise ValueError(f"{function_name} expects {expected_arity} argument(s)")
                arguments: list[str] = []
                for argument in node.args:
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                        value = _c_string(argument.value)
                    elif isinstance(argument, ast.Name) and argument.id.startswith("var_"):
                        name = argument.id[4:]
                        if name not in variable_indices:
                            raise ValueError(f"unknown variable '{name}'")
                        value = (
                            f"get_variable({context}, {variable_indices[name]}, {line})"
                        )
                    else:
                        raise ValueError(
                            f"{function_name} arguments must be string literals or variables"
                        )
                    arguments.append(f"copy_text({value})")
                if function_name == "read_text":
                    return f"read_text_file_owned({arguments[0]}, {line})"
                return (
                    f"((double)write_text_file_owned({arguments[0]}, "
                    f"{arguments[1]}, {line}))"
                )
            if complex_mode and function_name == "complex":
                if node.keywords or not 1 <= len(node.args) <= 2:
                    raise ValueError("complex expects one or two numeric arguments")
                real = convert(node.args[0])
                imag = convert(node.args[1]) if len(node.args) == 2 else "0.0"
                return f"(({real}) + ({imag}) * I)"
            if function_name == "err_count":
                if node.args or node.keywords:
                    raise ValueError("err_count takes no arguments")
                if bigint_mode:
                    return f"bigint_from_int64((int64_t)cosmic_error_count, {line})"
                return "((double)cosmic_error_count)"
            if function_name == "bytes_len":
                if node.keywords or len(node.args) != 1:
                    raise ValueError("bytes_len expects one bytes array")
                array = convert_array(node.args[0])
                return f"cosmic_array_length({array})"
            if function_name == "bytes_get":
                if node.keywords or len(node.args) != 2:
                    raise ValueError("bytes_get expects an array and an index")
                if not isinstance(node.args[0], ast.Name) or node.args[0].id not in array_indices:
                    raise ValueError("bytes_get expects a bytes array name")
                return (
                    f"cosmic_bytes_get({context}, {array_indices[node.args[0].id]}, "
                    f"(double)({convert(node.args[1])}), {line})"
                )
            if function_name == "hex":
                if node.keywords or len(node.args) not in {1, 2}:
                    raise ValueError("hex expects a value and an optional width")
                value_expr = convert(node.args[0])
                if len(node.args) == 1:
                    if bigint_mode:
                        return f"bigint_to_hex({value_expr}, 0, {line})"
                    return f"cosmic_hex((long long)({value_expr}), 0, {line})"
                width_expr = convert(node.args[1])
                if bigint_mode:
                    width_expr = (
                        f"(int)bigint_to_int64({width_expr}, {line})"
                    )
                if bigint_mode:
                    return f"bigint_to_hex({value_expr}, (int)({width_expr}), {line})"
                return f"cosmic_hex((long long)({value_expr}), (int)({width_expr}), {line})"
            if function_name == "unhex":
                if node.keywords or len(node.args) != 1:
                    raise ValueError("unhex expects exactly one hexadecimal string")
                if not bigint_mode:
                    raise ValueError("unhex requires bigint math mode")
                return f"bigint_from_hex({convert(node.args[0])}, {line})"
            if function_name == "chr":
                if node.keywords or len(node.args) != 1:
                    raise ValueError("chr expects exactly one integer code point")
                if bigint_mode:
                    return (
                        f"unicode_chr((double)bigint_to_int64({convert(node.args[0])}, {line}), {line})"
                    )
                return f"unicode_chr((double)({convert(node.args[0])}), {line})"
            if function_name == "ord":
                if node.keywords or len(node.args) != 1:
                    raise ValueError("ord expects exactly one Unicode character")
                argument = node.args[0]
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    character = _c_string(argument.value)
                    if bigint_mode:
                        return (
                            f"bigint_from_int64((int64_t)unicode_ord({character}, {line}), {line})"
                        )
                elif isinstance(argument, ast.Name) and argument.id.startswith("var_"):
                    name = argument.id[4:]
                    if name not in variable_indices:
                        raise ValueError(f"unknown variable '{name}'")
                    character = (
                        f"get_variable({context}, {variable_indices[name]}, {line})"
                    )
                    if bigint_mode:
                        return (
                            f"bigint_from_int64((int64_t)unicode_ord_owned({character}, {line}), {line})"
                        )
                    return f"((double)unicode_ord_owned({character}, {line}))"
                elif (
                    isinstance(argument, ast.Call)
                    and isinstance(argument.func, ast.Name)
                    and argument.func.id.lower() == "chr"
                ):
                    character = convert(argument)
                    if bigint_mode:
                        return (
                            f"bigint_from_int64((int64_t)unicode_ord_owned({character}, {line}), {line})"
                        )
                    return f"((double)unicode_ord_owned({character}, {line}))"
                else:
                    raise ValueError("ord expects a string literal, variable, or chr result")
                if bigint_mode:
                    return f"bigint_from_int64((int64_t)unicode_ord({character}, {line}), {line})"
                return f"((double)unicode_ord({character}, {line}))"
            if function_name == "strlen":
                if node.keywords or len(node.args) != 1:
                    raise ValueError("strlen expects exactly one string")
                argument = node.args[0]
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    text = _c_string(argument.value)
                elif isinstance(argument, ast.Name) and argument.id.startswith("var_"):
                    name = argument.id[4:]
                    if name not in variable_indices:
                        raise ValueError(f"unknown variable '{name}'")
                    text = f"get_variable({context}, {variable_indices[name]}, {line})"
                    if bigint_mode:
                        return (
                            f"bigint_from_int64((int64_t)unicode_strlen_owned({text}), {line})"
                        )
                    return f"((double)unicode_strlen_owned({text}))"
                elif (
                    isinstance(argument, ast.Call)
                    and isinstance(argument.func, ast.Name)
                    and argument.func.id.lower() == "char_at"
                ):
                    text = convert(argument)
                    if bigint_mode:
                        return (
                            f"bigint_from_int64((int64_t)unicode_strlen_owned({text}), {line})"
                        )
                    return f"((double)unicode_strlen_owned({text}))"
                else:
                    raise ValueError("strlen expects a string literal, variable, or char_at result")
                if bigint_mode:
                    return f"bigint_from_int64((int64_t)unicode_strlen({text}), {line})"
                return f"((double)unicode_strlen({text}))"
            if function_name == "char_at":
                if node.keywords or len(node.args) != 2:
                    raise ValueError("char_at expects a string and an index")
                string_argument, index_argument = node.args
                if isinstance(string_argument, ast.Constant) and isinstance(string_argument.value, str):
                    text = _c_string(string_argument.value)
                elif isinstance(string_argument, ast.Name) and string_argument.id.startswith("var_"):
                    name = string_argument.id[4:]
                    if name not in variable_indices:
                        raise ValueError(f"unknown variable '{name}'")
                    text = f"get_variable({context}, {variable_indices[name]}, {line})"
                    return (
                        f"unicode_char_at_owned({text}, "
                        f"(double)({convert(index_argument)}), {line})"
                    )
                else:
                    raise ValueError("char_at expects a string literal or variable")
                return (
                    f"unicode_char_at({text}, (double)({convert(index_argument)}), {line})"
                )
            if function_name == "rag_range":
                if node.keywords or len(node.args) != 2:
                    raise ValueError("rag requires max and min bounds")
                if bigint_mode:
                    return (
                        f"bigint_from_int64((int64_t)random_die("
                        f"(double)bigint_to_int64({convert(node.args[0])}, {line}), "
                        f"(double)bigint_to_int64({convert(node.args[1])}, {line}), {line}), {line})"
                    )
                return (
                    f"((double)random_die({convert(node.args[0])}, "
                    f"{convert(node.args[1])}, {line}))"
                )
            functions = {
                "abs": "fabs",
                "sqrt": "sqrt",
                "sin": "sin",
                "cos": "cos",
                "tan": "tan",
                "asin": "asin",
                "acos": "acos",
                "atan": "atan",
                "atan2": "atan2",
                "sinh": "sinh",
                "cosh": "cosh",
                "tanh": "tanh",
                "exp": "exp",
                "log": "log",
                "log10": "log10",
                "log2": "log2",
                "floor": "floor",
                "ceil": "ceil",
                "pow": "pow",
                "fmod": "fmod",
                "hypot": "hypot",
                "erf": "erf",
                "erfc": "erfc",
                "gamma": "tgamma",
                "lgamma": "lgamma",
            }
            if function_name == "beta":
                if node.keywords or len(node.args) != 2:
                    raise ValueError("beta expects two arguments")
                return (
                    f"cosmic_beta({convert(node.args[0])}, "
                    f"{convert(node.args[1])}, {line})"
                )
            if function_name == "zeta":
                if node.keywords or len(node.args) != 1:
                    raise ValueError("zeta expects one argument")
                return f"cosmic_riemann_zeta({convert(node.args[0])}, {line})"
            if complex_mode:
                complex_functions = {
                    "abs": "cabs",
                    "sqrt": "csqrt",
                    "sin": "csin",
                    "cos": "ccos",
                    "tan": "ctan",
                    "asin": "casin",
                    "acos": "cacos",
                    "atan": "catan",
                    "sinh": "csinh",
                    "cosh": "ccosh",
                    "tanh": "ctanh",
                    "exp": "cexp",
                    "log": "clog",
                    "log10": None,
                    "log2": None,
                    "pow": "cpow",
                    "real": "creal",
                    "imag": "cimag",
                    "conj": "conj",
                    "cabs": "cabs",
                }
                if function_name in {"log10", "log2"}:
                    argument = convert(node.args[0])
                    divisor = "log(10.0)" if function_name == "log10" else "log(2.0)"
                    return f"(clog({argument}) / {divisor})"
                if function_name == "log" and len(node.args) == 2:
                    return f"(clog({convert(node.args[0])}) / clog({convert(node.args[1])}))"
                if function_name in complex_functions:
                    function = complex_functions[function_name]
                    valid_arity = (
                        len(node.args) in {1, 2}
                        if function_name == "log"
                        else len(node.args) == (2 if function_name == "pow" else 1)
                    )
                    if function is None or node.keywords or not valid_arity:
                        expected_arity = 2 if function_name == "pow" else 1
                        raise ValueError(f"{function_name} expects {expected_arity} argument(s)")
                    arguments = ", ".join(convert(argument) for argument in node.args)
                    return f"{function}({arguments})"
            function = functions.get(function_name)
            if function is None or node.keywords:
                raise ValueError(f"unsupported scientific function '{node.func.id}'")
            if any(
                _is_array_ast_node(argument, array_names)
                for argument in node.args
            ):
                raise ValueError("array values must be passed to an array function")
            arguments = ", ".join(convert(argument) for argument in node.args)
            if function_name == "log" and len(node.args) == 2:
                arguments = [convert(argument) for argument in node.args]
                return f"(log({arguments[0]}) / log({arguments[1]}))"
            expected_arity = 2 if function_name in {"atan2", "pow", "fmod", "hypot"} else 1
            if len(node.args) != expected_arity:
                if function_name != "log" or len(node.args) != 2:
                    raise ValueError(
                        f"{function_name} expects {expected_arity} argument(s)"
                    )
            return f"{function}({arguments})"
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            if _is_array_ast_node(node.left, array_names) or any(
                _is_array_ast_node(value, array_names)
                for value in node.comparators
            ):
                raise ValueError("array values must be passed to an array function")
            operators = {
                ast.Eq: "==",
                ast.NotEq: "!=",
                ast.Lt: "<",
                ast.LtE: "<=",
                ast.Gt: ">",
                ast.GtE: ">=",
            }
            operator_text = operators.get(type(node.ops[0]))
            if operator_text is None:
                raise ValueError("unsupported comparison")
            if bigint_mode:
                return (
                    f"(bigint_compare({convert(node.left)}, "
                    f"{convert(node.comparators[0])}) {operator_text} 0)"
                )
            return f"({convert(node.left)} {operator_text} {convert(node.comparators[0])})"
        if isinstance(node, ast.BoolOp):
            operator_text = "&&" if isinstance(node.op, ast.And) else "||" if isinstance(node.op, ast.Or) else None
            if operator_text is None:
                raise ValueError("unsupported logical operator")
            return "(" + f" {operator_text} ".join(convert(value) for value in node.values) + ")"
        raise ValueError("unsupported expression syntax")

    try:
        if matrix_result:
            return convert_matrix(tree.body)
        return convert_array(tree.body) if array_mode else convert(tree.body)
    except ValueError as error:
        raise ParseError(line, f"invalid expression: {error}") from error


def _is_integer_expression(
    expression: str, function_names: set[str] | None = None
) -> bool:
    transformed, _variables = transform_braced_expression(expression)
    transformed = re.sub(r"tape\s*\(\s*([A-Za-z_]\w*)\s*\)", r"tape_\1", transformed, flags=re.I)
    try:
        tree = ast.parse(transformed.strip(), mode="eval")
    except SyntaxError:
        return False

    def is_integer_node(node: ast.expr) -> bool:
        if isinstance(node, ast.Constant):
            return type(node.value) is int
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id.lower()
            if name in {"bytes_get", "bytes_len", "array_at", "array_len"}:
                return not node.keywords
            return (
                function_names is not None
                and node.func.id in function_names
                and not node.keywords
                and all(is_integer_node(argument) for argument in node.args)
            )
        if isinstance(node, ast.Name):
            return node.id.startswith("var_") or node.id.startswith("tape_")
        if isinstance(node, ast.UnaryOp):
            return isinstance(node.op, (ast.UAdd, ast.USub, ast.Invert)) and is_integer_node(node.operand)
        if isinstance(node, ast.BinOp):
            return (
                isinstance(
                    node.op,
                    (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
                     ast.Pow, ast.BitAnd, ast.BitOr, ast.BitXor, ast.LShift,
                     ast.RShift),
                )
                and is_integer_node(node.left)
                and is_integer_node(node.right)
            )
        return False

    return is_integer_node(tree.body)


@dataclass(frozen=True)
class _Block:
    statement: Statement
    index: int
    c_name: str


class CGenerator:
    def __init__(self, program: Program, max_loop_iterations: int | None = None) -> None:
        self.program = program
        self.max_loop_iterations = max_loop_iterations
        self.blocks: list[_Block] = []
        self.block_by_statement: dict[int, _Block] = {}
        self.variable_names: set[str] = set()
        self.array_names: set[str] = set()
        self.array_value_names: set[str] = set()
        self.tape_names: set[str] = set()
        self.block_names_seen: set[str] = set()
        self.functions: dict[str, Statement] = {}
        self.uses_blas = False
        self.uses_lapack = False
        self.uses_dll = bool(program.extern_functions)
        self.bigint_call_overrides = False
        self.uses_atom = False
        self.atom_chains: dict[str, tuple[str, ...]] = {}
        self._scan(program.statements)
        self._validate_jumps(program.statements)
        self._validate_calls(program.statements)
        names = sorted(self.variable_names)
        self.variable_indices = {name: index for index, name in enumerate(names)}
        self.array_indices = {
            name: index for index, name in enumerate(sorted(self.array_names))
        }
        self.array_variable_indices = {
            "var_" + name: self.variable_indices[name]
            for name in sorted(self.array_value_names)
        }
        self.expression_array_names = set(self.array_indices) | set(self.array_variable_indices)
        self.tape_indices = {name: index for index, name in enumerate(sorted(self.tape_names))}
        self.block_names = {id(block.statement): block for block in self.blocks}

    def generate(self) -> str:
        variable_count = max(1, len(self.variable_indices))
        block_count = max(1, len(self.blocks))
        tape_count = max(1, len(self.tape_indices))
        array_count = max(1, len(self.array_indices))
        output = [
            "/* Feature macros must come before any system header. */",
            "#ifndef _GNU_SOURCE",
            "#define _GNU_SOURCE 1",
            "#endif",
            "#ifndef _DEFAULT_SOURCE",
            "#define _DEFAULT_SOURCE 1",
            "#endif",
            f"#define VAR_COUNT {variable_count}",
            f"#define BLOCK_COUNT {block_count}",
            f"#define TAPE_COUNT {tape_count}",
            f"#define ARRAY_COUNT {array_count}",
            _C_RUNTIME,
            "",
        ]
        if self.uses_blas:
            output.append(_BLAS_RUNTIME)
        if self.uses_lapack:
            output.append(_LAPACK_RUNTIME)
        if self.uses_dll:
            output.extend(self._generate_dll_declarations())
        if self.uses_dll:
            output.append(_DLL_RUNTIME)
        if self.uses_atom:
            output.append(_ATOM_RUNTIME)
            for block in self.blocks:
                output.append(
                    f"static void *cosmic_atom_thread_{block.index}(void *argument);"
                )
        for block in self.blocks:
            output.append(
                f"static void {block.c_name}(Context *parent, int inherit, Result *out);"
            )
        if self.uses_atom:
            for block in self.blocks:
                output.extend(
                    [
                        f"static void *cosmic_atom_thread_{block.index}(void *argument) {{",
                        f"    AtomBlock *block = (AtomBlock *)argument;",
                        f"    Result local = {{ STATUS_OK, NULL }};",
                        f"    cosmic_current_stop = &block->stop;",
                        f"    block->runner(NULL, 0, &local);",
                        f"    cosmic_current_stop = NULL;",
                        f"    cosmic_mutex_lock(&block->state_lock);",
                        f"    block->state = block->stop ? ATOM_STOPPED : (local.status == STATUS_OK ? ATOM_OK : (local.status == STATUS_STOP ? ATOM_STOP : ATOM_ERROR));",
                        f"    cosmic_mutex_unlock(&block->state_lock);",
                        f"    free(local.value);",
                        f"    return NULL;",
                        f"}}",
                        "",
                    ]
                )
        needs_bigint_variants = (
            self.bigint_call_overrides and self.program.math_mode != "bigint"
        )
        if needs_bigint_variants:
            for name, statement in self.functions.items():
                parameters = statement.data["parameters"]
                signature = ", ".join(
                    "BigInt param_" + str(value) for value in parameters
                )
                output.append(
                    f"static BigInt user_{name}_bigint({signature or 'void'});"
                )
        for name, statement in self.functions.items():
            parameters = statement.data["parameters"]
            value_type = (
                "BigInt" if self.program.math_mode == "bigint"
                else "uint32_t" if self.program.math_mode == "uint32"
                else "double complex" if self.program.math_mode == "complex"
                else "double"
            )
            signature = ", ".join(value_type + " param_" + str(value) for value in parameters)
            output.append(f"static {value_type} user_{name}({signature or 'void'});")
        if self.functions:
            output.append("static unsigned int cosmic_function_depth = 0;")
        output.append("")
        for name, statement in self.functions.items():
            output.extend(self._generate_function(name, statement))
        if needs_bigint_variants:
            for name, statement in self.functions.items():
                output.extend(self._generate_bigint_function(name, statement))
        for block in self.blocks:
            output.extend(self._generate_block(block))
        output.extend(self._generate_main())
        return "\n".join(output)

    def _scan(self, statements: tuple[Statement, ...]) -> None:
        for statement in statements:
            if statement.kind == "function":
                name = str(statement.data["name"]).lower()
                if name in self.functions:
                    raise ParseError(statement.line, f"duplicate function name '{name}'")
                if name in {"choose", "complex"} or name in _BUILTIN_FUNCTION_NAMES:
                    raise ParseError(statement.line, f"function name '{name}' is reserved")
                self.functions[name] = statement
                self._scan_expression_names(str(statement.data["expression"]))
                continue
            if statement.kind == "encoding":
                self._validate_encoding(str(statement.data["encoding"]), statement.line)
            elif statement.kind == "push" and statement.data.get("encoding"):
                self._validate_encoding(str(statement.data["encoding"]), statement.line)
            elif statement.kind == "print" and statement.data.get("encoding"):
                self._validate_encoding(str(statement.data["encoding"]), statement.line)
            if statement.kind in {"array_declare", "bytes_declare"}:
                name = str(statement.data["name"])
                self.array_names.add(name)
                self._scan_expression_names(str(statement.data["expression"]))
            if statement.kind == "start":
                block_name = str(statement.data["name"])
                if block_name in self.block_names_seen:
                    raise ParseError(statement.line, f"duplicate execution block name '{block_name}'")
                self.block_names_seen.add(block_name)
                block = _Block(statement, len(self.blocks), f"block_{len(self.blocks)}")
                self.blocks.append(block)
                self.block_by_statement[id(statement)] = block
                self._scan(statement.body)
                else_body = statement.data.get("else_body", ())
                if isinstance(else_body, tuple):
                    self._scan(else_body)
            elif statement.kind in {"calldll", "cdll"}:
                self.uses_dll = True
            elif statement.kind in {"lock", "n_bomb", "neutron", "proton", "nuclear_reactor"}:
                self.uses_atom = True
                if statement.kind == "proton":
                    chain_name = str(statement.data["chain"])
                    self.atom_chains[chain_name] = tuple(
                        str(name) for name in statement.data["blocks"]
                    )
                if statement.kind == "lock":
                    for child in statement.body:
                        self._scan_atom_body(child)
            elif statement.kind in {"while", "if_expr"}:
                self._scan(statement.body)
            elif statement.kind in {"encoding", "push", "kill", "command", "var_declare"}:
                self.variable_names.add(str(statement.data["name"]))
                if statement.kind == "push":
                    value_spec = statement.data["value"]
                    if isinstance(value_spec, dict):
                        self._scan_value_variables(value_spec)
                        if value_spec.get("kind") == "array_expression":
                            name = str(statement.data["name"])
                            self.array_value_names.add(name)
                            expression = str(value_spec.get("expression", ""))
                            self._scan_expression_names(expression)
                            try:
                                transformed, _ = transform_braced_expression(expression)
                                tree = ast.parse(transformed, mode="eval")
                            except (SyntaxError, ValueError):
                                pass
                            else:
                                self.array_value_names.update(
                                    reference[4:]
                                    for reference in _array_variable_references(tree.body)
                                )
                        elif (
                            value_spec.get("kind") == "variable"
                            and str(value_spec.get("name", "")) in self.array_value_names
                        ):
                            self.array_value_names.add(str(statement.data["name"]))
            elif statement.kind == "print":
                value_spec = statement.data.get("value")
                if isinstance(value_spec, dict):
                    self._scan_value_variables(value_spec)
            elif statement.kind == "if":
                selector = str(statement.data["selector"])
                if selector not in {"status", "*"}:
                    self.variable_names.add(selector)
                target = statement.data["target"]
                if isinstance(target, dict) and target.get("kind") == "push":
                    self.variable_names.add(str(target["name"]))
                    value_spec = target.get("value")
                    if isinstance(value_spec, dict):
                        self._scan_value_variables(value_spec)
                    if target.get("encoding"):
                        self._validate_encoding(str(target["encoding"]), statement.line)
            elif statement.kind == "math":
                self._scan_expression_names(str(statement.data["expression"]))
                if str(statement.data.get("precision") or "") == "bigint":
                    self.bigint_call_overrides = True
            elif statement.kind in {"tape_create", "tape_move", "tape_write", "tape_read"}:
                self.tape_names.add(str(statement.data["name"]))
            if statement.kind in {"while", "if_expr"}:
                self._scan_expression_names(str(statement.data["condition"]))

    def _matrix_expression(
        self,
        expression: str,
        context: str,
        line: int,
        variable_indices: dict[str, int],
        tape_indices: dict[str, int],
        *,
        array_indices: dict[str, int],
        array_variable_indices: dict[str, int],
        program_math_mode: str,
    ) -> str | None:
        """Return a C matrix expression when the source is matrix-shaped, else None."""
        if program_math_mode == "complex":
            return None
        try:
            transformed, _ = transform_braced_expression(expression)
            tree = ast.parse(transformed.strip(), mode="eval")
        except (SyntaxError, ValueError):
            return None
        body = tree.body
        is_matrix = False
        if isinstance(body, ast.List) and body.elts:
            is_matrix = all(isinstance(element, ast.List) for element in body.elts)
        elif isinstance(body, ast.Call) and isinstance(body.func, ast.Name):
            is_matrix = body.func.id.lower() in {
                "matrix_mul", "matrix_transpose", "matrix2_inverse", "matrix3_inverse",
            }
        if not is_matrix:
            return None
        return _c_expression(
            expression,
            context,
            line,
            variable_indices,
            tape_indices,
            array_indices=array_indices,
            array_variable_indices=array_variable_indices,
            matrix_result=True,
        )

    def _scan_expression_names(self, expression: str) -> None:
        try:
            transformed, variables = transform_braced_expression(expression)
        except ValueError:
            transformed = expression
            variables = set()
        self.variable_names.update(variables)
        self.tape_names.update(
            re.findall(r"tape\s*\(\s*([A-Za-z_]\w*)\s*\)", expression, re.I)
        )
        try:
            tree = ast.parse(transformed, mode="eval")
        except (SyntaxError, ValueError):
            return
        call_names = {
            node.func.id.lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.uses_blas |= bool(call_names & {"blas_dot"})
        self.uses_lapack |= bool(call_names & {"lapack_determinant2"})

    def _scan_atom_body(self, statement: Statement) -> None:
        if statement.kind in {"encoding", "push", "kill", "command", "var_declare"}:
            self.variable_names.add(str(statement.data["name"]))
            value_spec = statement.data.get("value")
            if statement.kind == "push" and isinstance(value_spec, dict):
                self._scan_value_variables(value_spec)
                if value_spec.get("kind") == "array_expression":
                    self.array_value_names.add(str(statement.data["name"]))
                    self._scan_expression_names(str(value_spec.get("expression", "")))
        elif statement.kind == "math":
            self._scan_expression_names(str(statement.data["expression"]))
        elif statement.kind == "array_declare":
            self.array_names.add(str(statement.data["name"]))
            self._scan_expression_names(str(statement.data["expression"]))
        elif statement.kind == "if":
            selector = str(statement.data["selector"])
            if selector not in {"status", "*"}:
                self.variable_names.add(selector)
            target = statement.data["target"]
            if isinstance(target, dict) and target.get("kind") == "push":
                self.variable_names.add(str(target["name"]))
                value_spec = target.get("value")
                if isinstance(value_spec, dict):
                    self._scan_value_variables(value_spec)
        elif statement.kind == "print":
            value_spec = statement.data.get("value")
            if isinstance(value_spec, dict):
                self._scan_value_variables(value_spec)
        elif statement.kind in {"if_expr", "while", "lock"}:
            self._scan_expression_names(str(statement.data.get("condition", "")))
            for child in statement.body:
                self._scan_atom_body(child)

    def _scan_value_variables(self, spec: dict[str, object]) -> None:
        if spec.get("kind") == "variable":
            self.variable_names.add(str(spec.get("name", "")))
        elif spec.get("kind") == "concat":
            parts = spec.get("parts")
            if isinstance(parts, list):
                for part in parts:
                    if isinstance(part, dict):
                        self._scan_value_variables(part)

    def _validate_jumps(self, statements: tuple[Statement, ...]) -> None:
        for statement in statements:
            if statement.kind != "start":
                if statement.kind in {"while", "if_expr"}:
                    self._validate_jumps(statement.body)
                continue
            direct_statements = {child.line: child for child in statement.body}

            def validate_nested(children: tuple[Statement, ...]) -> None:
                for child in children:
                    if child.kind == "jump":
                        target = int(child.data["line"])
                        target_statement = direct_statements.get(target)
                        if target_statement is None or target_statement.kind in {
                            "start", "while", "if_expr"
                        }:
                            raise ParseError(
                                child.line,
                                f"jump target line {target} must be a direct, non-control statement in this execution block",
                            )
                    elif child.kind in {"while", "if_expr"}:
                        validate_nested(child.body)
                    elif child.kind == "start":
                        self._validate_jumps((child,))

            validate_nested(statement.body)
            else_body = statement.data.get("else_body", ())
            if isinstance(else_body, tuple):
                def find_else_jump(children: tuple[Statement, ...]) -> Statement | None:
                    for child in children:
                        if child.kind == "jump":
                            return child
                        if child.kind in {"while", "if_expr"}:
                            nested_jump = find_else_jump(child.body)
                            if nested_jump is not None:
                                return nested_jump
                    return None

                jump = find_else_jump(else_body)
                if jump is not None:
                    raise ParseError(jump.line, "jump is not supported inside an execution block else branch")
                validate_nested(else_body)

    def _generate_function(self, name: str, statement: Statement) -> list[str]:
        parameters = tuple(str(value) for value in statement.data["parameters"])
        value_type = (
            "BigInt" if self.program.math_mode == "bigint"
            else "uint32_t" if self.program.math_mode == "uint32"
            else "double complex" if self.program.math_mode == "complex"
            else "double"
        )
        signature = ", ".join(value_type + " param_" + value for value in parameters)
        if self.program.math_mode == "bigint":
            try:
                transformed, _ = transform_braced_expression(
                    str(statement.data["expression"])
                )
                function_tree = ast.parse(
                    normalize_rag_calls(transformed), mode="eval"
                )
            except (SyntaxError, ValueError) as error:
                raise ParseError(statement.line, f"invalid bigint function expression: {error}") from error
            if not _is_bigint_result_node(
                function_tree.body,
                set(parameters),
                set(self.functions),
            ):
                raise ParseError(
                    statement.line,
                    "bigint mode requires integer-only function expressions",
                )
        expression = _c_expression(
            str(statement.data["expression"]),
            "context",
            statement.line,
            self.variable_indices,
            self.tape_indices,
            user_functions={
                function_name: len(tuple(function.data["parameters"]))
                for function_name, function in self.functions.items()
            },
            parameters=set(parameters),
            array_indices=self.array_indices,
            array_variable_indices=self.array_variable_indices,
            bigint_mode=self.program.math_mode == "bigint",
            uint32_mode=self.program.math_mode == "uint32",
            complex_mode=self.program.math_mode == "complex",
        )
        return [
            f"static {value_type} user_{name}({signature or 'void'}) {{",
            f"    if (cosmic_function_depth >= 256) fail_at({statement.line}, \"function recursion depth exceeded\");",
            "    ++cosmic_function_depth;",
            f"    {value_type} result = ({value_type})({expression});",
            "    --cosmic_function_depth;",
            "    return result;",
            "}",
            "",
        ]

    def _generate_bigint_function(self, name: str, statement: Statement) -> list[str]:
        parameters = tuple(str(value) for value in statement.data["parameters"])
        signature = ", ".join("BigInt param_" + value for value in parameters)
        expression = _c_expression(
            str(statement.data["expression"]),
            "context",
            statement.line,
            self.variable_indices,
            self.tape_indices,
            user_functions={
                function_name: len(tuple(function.data["parameters"]))
                for function_name, function in self.functions.items()
            },
            parameters=set(parameters),
            array_indices=self.array_indices,
            array_variable_indices=self.array_variable_indices,
            bigint_mode=True,
            bigint_function_suffix="_bigint",
        )
        return [
            f"static BigInt user_{name}_bigint({signature or 'void'}) {{",
            f"    if (cosmic_function_depth >= 256) fail_at({statement.line}, \"function recursion depth exceeded\");",
            "    ++cosmic_function_depth;",
            f"    BigInt result = {expression};",
            "    --cosmic_function_depth;",
            "    return result;",
            "}",
            "",
        ]

    def _validate_calls(
        self,
        statements: tuple[Statement, ...],
        inherited_imports: set[str] | None = None,
    ) -> None:
        imports = set(inherited_imports or ())
        pending_inheritance = False
        for statement in statements:
            if statement.kind == "import":
                component = str(statement.data["component"])
                if component != "pe":
                    imports.add(component)
            elif statement.kind == "call":
                component = str(statement.data["component"])
                if component == "pe":
                    raise ParseError(statement.line, "pe is recognized but not implemented")
                if component not in imports and component not in {"execute", "math"}:
                    raise ParseError(statement.line, f"component '{component}' has not been imported")
            elif statement.kind == "father":
                pending_inheritance = True
            elif statement.kind == "start":
                self._validate_calls(
                    statement.body,
                    imports if pending_inheritance else set(),
                )
                pending_inheritance = False
            elif statement.kind in {"if_expr", "while"}:
                self._validate_calls(statement.body, imports)

    @staticmethod
    def _validate_encoding(encoding: str, line: int) -> None:
        try:
            canonical = codecs.lookup(encoding).name
        except LookupError as error:
            raise ParseError(line, f"unknown encoding: {encoding}") from error
        if canonical not in {"utf-8", "gbk", "ascii", "cp1252"}:
            raise ParseError(
                line,
                f"encoding '{encoding}' is not supported by the C backend (use UTF-8, GBK, ASCII, or CP1252)",
            )

    def _generate_block(self, block: _Block) -> list[str]:
        mode = str(block.statement.data["mode"])
        lines = [
            f"static void {block.c_name}(Context *parent, int inherit, Result *out) {{",
            "    int owns_context = 1;",
            "    Context *context = NULL;",
        ]
        if self.uses_atom:
            lines.extend(
                [
                    "    cosmic_atom_init();",
                    f"    if (cosmic_atoms[{block.index}].has_context && cosmic_atoms[{block.index}].context) {{",
                    f"        context = cosmic_atoms[{block.index}].context;",
                    f"        cosmic_atoms[{block.index}].context = NULL;",
                    f"        cosmic_atoms[{block.index}].has_context = 0;",
                    "        owns_context = 0;",
                    "    } else {",
                    "        context = (Context *)calloc(1, sizeof(Context));",
                    "        if (!context) fail_at(0, \"out of memory\");",
                    "        if (inherit && parent) inherit_context(context, parent);",
                    "    }",
                ]
            )
        else:
            lines.extend(
                [
                    "    context = (Context *)calloc(1, sizeof(Context));",
                    "    if (!context) fail_at(0, \"out of memory\");",
                    "    if (inherit && parent) inherit_context(context, parent);",
                ]
            )
        lines.extend(
            [
                "    Result result = { STATUS_OK, NULL };",
                "    int stopped = 0;",
            ]
        )
        if mode == "loop":
            if self.max_loop_iterations is None:
                lines.append("    while (1) {")
            else:
                lines.append("    int iteration;")
                lines.append(f"    for (iteration = 0; iteration < {self.max_loop_iterations}; ++iteration) {{")
            indent = "        "
        else:
            indent = "    "
        pending_inheritance = False
        for statement in block.statement.body:
            lines.append(f"{indent}line_{statement.line}: ;")
            if statement.kind == "father":
                pending_inheritance = True
                lines.extend(
                    indent + line
                    for line in self._generate_statement(
                        statement,
                        context="context",
                        result="result",
                        block_mode=mode,
                        inherit=False,
                    )
                )
                continue
            lines.extend(
                indent + line
                for line in self._generate_statement(
                    statement,
                    context="context",
                    result="result",
                    block_mode=mode,
                    inherit=pending_inheritance,
                )
            )
            if statement.kind == "start":
                pending_inheritance = False
        if mode == "loop" and self.max_loop_iterations is not None:
            lines.extend(
                [
                    "        if (stopped) break;",
                    "    }",
                    "    if (!stopped) fail_at(%d, \"Loop_Deadlock: block exceeded iteration limit\");"
                    % block.statement.line,
                ]
            )
        elif mode == "loop":
            lines.extend(["        if (stopped) break;", "    }"])
        lines.append("block_primary_done:")
        else_body = block.statement.data.get("else_body", ())
        if isinstance(else_body, tuple) and else_body:
            lines.append("    if (result.status != STATUS_OK) {")
            for child in else_body:
                lines.extend(
                    "        " + generated
                    for generated in self._generate_statement(
                        child,
                        context="context",
                        result="result",
                        block_mode=mode,
                        inherit=False,
                    )
                )
            lines.append("    }")
        lines.extend(
            [
                "block_done:",
                f"    block_status[{block.index}] = result.status;",
                "    latest_block_status = result.status;",
                f"    set_result(out, result.status, {_c_string(str(block.statement.data['name']))});",
                "    if (owns_context) {",
                "        free_context(context);",
                "        free(context);",
                "    }",
                "    free(result.value);",
                "}",
                "",
            ]
        )
        return lines

    def _generate_main(self) -> list[str]:
        lines = [
            "int main(void) {",
            "    Context storage = {0};",
            "    Context *context = &storage;",
            "    Result result = { STATUS_OK, NULL };",
            "    for (int i = 0; i < BLOCK_COUNT; ++i) block_status[i] = STATUS_ERROR;",
        ]
        pending_inheritance = False
        for statement in self.program.statements:
            if statement.kind == "father":
                pending_inheritance = True
                lines.extend(
                    "    " + line
                    for line in self._generate_statement(
                        statement,
                        context="context",
                        result="result",
                        block_mode=None,
                        inherit=False,
                    )
                )
                continue
            lines.extend(
                "    " + line
                for line in self._generate_statement(
                    statement,
                    context="context",
                    result="result",
                    block_mode=None,
                    inherit=pending_inheritance,
                )
            )
            if statement.kind == "start":
                pending_inheritance = False
        if self.uses_atom:
            lines.append("    cosmic_atom_release_all();")
        lines.extend(
            [
                "    free_context(context);",
                "    free(result.value);",
                "    free_tapes();",
                "    return 0;",
                "}",
            ]
        )
        return lines

    def _generate_statement(
        self,
        statement: Statement,
        *,
        context: str,
        result: str,
        block_mode: str | None,
        inherit: bool,
    ) -> list[str]:
        kind = statement.kind
        line = statement.line
        if kind == "father":
            return [f"set_result(&{result}, STATUS_OK, NULL);"]
        if kind == "function":
            return [
                f"set_result(&{result}, STATUS_OK, {_c_string(str(statement.data['name']))});"
            ]
        if kind == "array_declare" or kind == "bytes_declare":
            name = str(statement.data["name"])
            source_text = str(statement.data["expression"])
            matrix_source = self._matrix_expression(
                source_text,
                context,
                line,
                self.variable_indices,
                self.tape_indices,
                array_indices=self.array_indices,
                array_variable_indices=self.array_variable_indices,
                program_math_mode=self.program.math_mode,
            )
            if matrix_source is not None:
                return [
                    f"cosmic_store_matrix({context}, {self.array_indices[name]}, {matrix_source}, {line});",
                    "cosmic_release_arrays();",
                    f"set_result(&{result}, STATUS_OK, {_c_string(name)});",
                ]
            expression = _c_expression(
                str(statement.data["expression"]),
                context,
                line,
                self.variable_indices,
                self.tape_indices,
                array_indices=self.array_indices,
                array_variable_indices=self.array_variable_indices,
                complex_mode=self.program.math_mode == "complex",
                array_mode=True,
                user_functions={
                    function_name: len(tuple(function.data["parameters"]))
                    for function_name, function in self.functions.items()
                },
            )
            return [
                (
                    f"cosmic_store_complex_array({context}, {self.array_indices[name]}, {expression}, {line});"
                    if self.program.math_mode == "complex"
                    else f"cosmic_store_array({context}, {self.array_indices[name]}, {expression}, {line});"
                ),
                    "cosmic_release_arrays();",
                    f"set_result(&{result}, STATUS_OK, {_c_string(name)});",
            ]
        if kind == "var_declare":
            name = str(statement.data["name"])
            index = self.variable_indices[name]
            value_spec = statement.data["value"]
            if not isinstance(value_spec, dict) or value_spec.get("kind") != "literal":
                raise ParseError(line, "var declaration requires a quoted string literal")
            value = str(value_spec.get("value", ""))
            return [
                f"if ({context}->vars[{index}].initialized) fail_at({line}, \"variable '{name}' is already declared\");",
                f"set_variable({context}, {index}, {_c_string(value)}, {context}->vars[{index}].encoding, {line});",
                f"set_result(&{result}, STATUS_OK, {_c_string(value)});",
            ]
        if kind == "import":
            component = str(statement.data["component"])
            if component == "pe":
                return [
                    f"fprintf(stderr, \"warning: line {line}: pe is recognized but not implemented\\n\");",
                    f"set_result(&{result}, STATUS_OK, {_c_string(component)});",
                ]
            return [
                f"set_result(&{result}, STATUS_OK, {_c_string(component)});",
            ]
        if kind == "encoding":
            encoding = str(statement.data["encoding"])
            try:
                codecs.lookup(encoding)
            except LookupError as error:
                raise ParseError(line, f"unknown encoding: {encoding}") from error
            index = self.variable_indices[str(statement.data["name"])]
            return [
                f"{context}->vars[{index}].encoding = {_c_string(encoding)};",
                f"{context}->vars[{index}].initialized = 0;",
                f"{context}->vars[{index}].killed = 0;",
                f"set_result(&{result}, STATUS_OK, {_c_string(encoding)});",
            ]
        if kind == "push":
            index = self.variable_indices[str(statement.data["name"])]
            encoding = statement.data.get("encoding")
            encoding_expr = _c_string(str(encoding)) if encoding else f"{context}->vars[{index}].encoding"
            value_spec = statement.data["value"]
            if isinstance(value_spec, dict) and value_spec.get("kind") == "array_expression":
                source_text = str(value_spec.get("expression", ""))
                matrix_source = self._matrix_expression(
                    source_text,
                    context,
                    line,
                    self.variable_indices,
                    self.tape_indices,
                    array_indices=self.array_indices,
                    array_variable_indices=self.array_variable_indices,
                    program_math_mode=self.program.math_mode,
                )
                if matrix_source is not None:
                    return [
                        f"set_matrix_variable({context}, {index}, {matrix_source}, {line});",
                        "cosmic_release_arrays();",
                        f"set_result(&{result}, STATUS_OK, NULL);",
                    ]
                expression = _c_expression(
                    source_text,
                    context,
                    line,
                    self.variable_indices,
                    self.tape_indices,
                    array_indices=self.array_indices,
                    array_variable_indices=self.array_variable_indices,
                    complex_mode=self.program.math_mode == "complex",
                    array_mode=True,
                )
                store_function = (
                    "set_complex_array_variable"
                    if self.program.math_mode == "complex"
                    else "set_array_variable"
                )
                return [
                    f"{store_function}({context}, {index}, {expression}, {line});",
                    "cosmic_release_arrays();",
                    f"set_result(&{result}, STATUS_OK, NULL);",
                ]
            if isinstance(value_spec, dict) and value_spec.get("kind") == "concat":
                parts = value_spec.get("parts")
                if not isinstance(parts, list):
                    raise ParseError(line, "invalid string concatenation")
                expressions: list[str] = []
                for part in parts:
                    if not isinstance(part, dict):
                        raise ParseError(line, "invalid string concatenation part")
                    if part.get("kind") == "literal":
                        expressions.append(f"copy_text({_c_string(str(part.get('value', '')))})")
                    elif part.get("kind") == "variable":
                        variable_name = str(part.get("name", ""))
                        if variable_name not in self.variable_indices:
                            raise ParseError(line, f"unknown variable '{variable_name}'")
                        expressions.append(
                            f"get_variable({context}, {self.variable_indices[variable_name]}, {line})"
                        )
                    elif part.get("kind") == "up":
                        expressions.append(f"copy_text({result}.value)")
                    else:
                        raise ParseError(line, "unsupported string concatenation part")
                parts_array = ", ".join(expressions)
                checks = [
                    f"if (!parts[{part_index}]) fail_at({line}, \"variable '{part['name']}' is not declared or is empty\");"
                    for part_index, part in enumerate(parts)
                    if isinstance(part, dict) and part.get("kind") == "variable"
                ]
                checks.extend(
                    f"if (!parts[{part_index}]) fail_at({line}, \"up has no value\");"
                    for part_index, part in enumerate(parts)
                    if isinstance(part, dict) and part.get("kind") == "up"
                )
                return [
                    f"{{ char *parts[] = {{{parts_array}}};",
                    *checks,
                    f"  char *joined = concat_texts(parts, {len(expressions)}, {line});",
                    f"  set_variable({context}, {index}, joined, {encoding_expr}, {line});",
                    f"  set_result(&{result}, STATUS_OK, joined); free(joined); }}",
                ]
            if isinstance(value_spec, dict) and value_spec.get("kind") == "variable":
                source_name = str(value_spec.get("name", ""))
                source_index = self.variable_indices[source_name]
                return [
                    f"{{ char *source_value = copy_variable_value({context}, {index}, {source_index}, {encoding_expr}, {line});",
                    f"  set_result(&{result}, STATUS_OK, source_value); free(source_value); }}",
                ]
            target = self._target_expression(value_spec, context, result, line)
            return [
                f"set_variable({context}, {index}, {target}, {encoding_expr}, {line});",
                f"set_result(&{result}, STATUS_OK, {target});",
            ]
        if kind == "call":
            component = str(statement.data["component"])
            if component == "pe":
                return [f"fail_at({line}, \"pe is recognized but not implemented\");"]
            return [
                f"set_result(&{result}, STATUS_OK, {_c_string(component)});",
            ]
        if kind == "start":
            block = self.block_by_statement[id(statement)]
            if self.uses_atom:
                setup = (
                    [
                        f"if (!cosmic_atoms[{block.index}].has_context) {{",
                        f"    cosmic_atoms[{block.index}].context = {context};",
                        f"    cosmic_atoms[{block.index}].has_context = 1;",
                        f"    cosmic_atoms[{block.index}].shared = 1;",
                        f"}}",
                    ]
                    if inherit
                    else [
                        f"if (!cosmic_atoms[{block.index}].has_context) {{",
                        f"    Context *shared = (Context *)calloc(1, sizeof(Context));",
                        f"    if (!shared) fail_at({line}, \"out of memory\");",
                        f"    inherit_context(shared, {context});",
                        f"    cosmic_atoms[{block.index}].context = shared;",
                        f"    cosmic_atoms[{block.index}].has_context = 1;",
                        f"    cosmic_atoms[{block.index}].shared = 0;",
                        f"}}",
                    ]
                )
                return [
                    f"cosmic_atoms[{block.index}].runner = {block.c_name};",
                    *setup,
                    f"set_result(&{result}, STATUS_OK, {_c_string(str(statement.data['name']))});",
                ]
            return [
                "{",
                "    Result child_result = { STATUS_OK, NULL };",
                f"    {block.c_name}({context}, {1 if inherit else 0}, &child_result);",
                f"    copy_result(&{result}, &child_result);",
                "    free(child_result.value);",
                "}",
            ]
        if kind == "dog":
            name = str(statement.data["name"])
            if name == "status":
                status_expr = "latest_block_status"
            else:
                matching = [block for block in self.blocks if block.statement.data["name"] == name]
                if not matching:
                    raise ParseError(line, f"dog refers to unknown execution block '{name}'")
                atom_index = matching[-1].index
                if self.uses_atom:
                    status_expr = f"cosmic_atom_dog({atom_index})"
                else:
                    status_expr = f"block_status[{atom_index}]"
            mappings = statement.data.get("mappings")
            if isinstance(mappings, tuple):
                lines = []
                for index, (accepted, outcome) in enumerate(mappings):
                    accepted_status = _dog_status_constant(accepted)
                    outcome_status = _dog_status_constant(outcome)
                    keyword = "if" if index == 0 else "else if"
                    lines.append(
                        f"{keyword} ({status_expr} == {accepted_status}) "
                        f"set_result(&{result}, {outcome_status}, {_c_string(accepted)});"
                    )
                lines.append(
                    f"else fail_at({line}, \"Loop_Deadlock: dog status is not in whitelist\");"
                )
                return lines
            required = str(statement.data["condition"])
            expected_status = _dog_status_constant(required)
            return [
                f"if ({status_expr} != {expected_status}) fail_at({line}, \"Loop_Deadlock: dog condition did not match\");",
                f"set_result(&{result}, STATUS_OK, NULL);",
            ]
        if kind == "nop":
            return [f"set_result(&{result}, STATUS_OK, NULL);"]
        if kind == "jump":
            target = int(statement.data["line"])
            if block_mode is None:
                raise ParseError(line, "jump is only valid inside an execution block")
            return [
                f"goto line_{target};",
            ]
        if kind == "math":
            mode = str(statement.data.get("precision") or self.program.math_mode)
            source_expression = str(statement.data["expression"])
            if _is_array_value_expression(source_expression, self.expression_array_names):
                raise ParseError(
                    line,
                    "array values cannot be used as scalar math_run results; pass the array to an array function",
                )
            if mode == "bigint" and _is_string_expression(source_expression):
                expression = _c_expression(
                    source_expression,
                    context,
                    line,
                    self.variable_indices,
                    self.tape_indices,
                    array_indices=self.array_indices,
                    array_variable_indices=self.array_variable_indices,
                    bigint_mode=True,
                )
                return [
                    f"{{ char *computed = {expression};",
                    f"  set_result(&{result}, STATUS_OK, computed); free(computed); bigint_release_all(); }}",
                ]
            if mode == "bigint":
                try:
                    transformed, _ = transform_braced_expression(source_expression)
                    bigint_tree = ast.parse(
                        normalize_rag_calls(transformed), mode="eval"
                    )
                except (SyntaxError, ValueError) as error:
                    raise ParseError(line, f"invalid bigint expression: {error}") from error
                # A per-expression -bigint override may call user functions even
                # when the program's global mode is float64; those calls are
                # routed to the BigInt-typed wrappers generated below.
                supported_functions = set(self.functions)
                self.bigint_call_overrides = True
                if not _is_bigint_result_node(bigint_tree.body, set(), supported_functions):
                    raise ParseError(line, "bigint mode requires integer-only expressions")
                expression = _c_expression(
                    source_expression,
                    context,
                    line,
                    self.variable_indices,
                    self.tape_indices,
                    bigint_mode=True,
                    bigint_function_suffix=(
                        "_bigint" if self.program.math_mode != "bigint" else ""
                    ),
                    array_indices=self.array_indices,
                    array_variable_indices=self.array_variable_indices,
                    user_functions={
                        name: len(tuple(function.data["parameters"]))
                        for name, function in self.functions.items()
                    },
                )
                return [
                    f"{{ BigInt computed = {expression}; char *number = bigint_to_string(computed, {line});",
                    f"  set_result(&{result}, STATUS_OK, number); free(number); bigint_release_all(); }}",
                ]
            if mode == "complex":
                expression = _c_expression(
                    source_expression,
                    context,
                    line,
                    self.variable_indices,
                    self.tape_indices,
                    complex_mode=True,
                    array_indices=self.array_indices,
                    array_variable_indices=self.array_variable_indices,
                    user_functions={
                        name: len(tuple(function.data["parameters"]))
                        for name, function in self.functions.items()
                    },
                )
                return [
                    f"{{ double complex computed = (double complex)({expression}); char number[256];",
                    f"  if (!isfinite(creal(computed)) || !isfinite(cimag(computed))) fail_at({line}, \"invalid complex result\");",
                    f"  snprintf(number, sizeof(number), \"%.*g%+.*gi\", {self.program.output_precision}, creal(computed), {self.program.output_precision}, cimag(computed));",
                    f"  set_result(&{result}, STATUS_OK, number); }}",
                ]
            if _is_string_expression(source_expression):
                expression = _c_expression(
                    source_expression,
                    context,
                    line,
                    self.variable_indices,
                    self.tape_indices,
                    array_indices=self.array_indices,
                    array_variable_indices=self.array_variable_indices,
                )
                return [
                    f"{{ char *computed = {expression};",
                    f"  set_result(&{result}, STATUS_OK, computed); free(computed); }}",
                ]
            normalize = ""
            if mode == "uint32":
                if not _is_integer_expression(
                    source_expression, set(self.functions)
                ):
                    raise ParseError(
                        line, "uint32 mode requires an integer-only expression"
                    )
                expression = _c_expression(
                    source_expression,
                    context,
                    line,
                    self.variable_indices,
                    self.tape_indices,
                    integer_mode=True,
                    array_indices=self.array_indices,
                    array_variable_indices=self.array_variable_indices,
                    uint32_mode=True,
                    user_functions={
                        name: len(tuple(function.data["parameters"]))
                        for name, function in self.functions.items()
                    },
                )
                return [
                    f"{{ uint32_t raw = (uint32_t)({expression}); char number[32];",
                    f"  snprintf(number, sizeof(number), \"%u\", (unsigned int)raw);",
                    f"  set_result(&{result}, STATUS_OK, number); cosmic_release_arrays(); }}",
                ]
            integer_expression = mode in {"int32", "int64"} and _is_integer_expression(
                source_expression
            )
            if mode == "int32":
                minimum, maximum = -(2**31), 2**31 - 1
                normalize = (
                    f"if (computed != trunc(computed) || computed < {minimum}.0 || computed > {maximum}.0) "
                    f"fail_at({line}, \"result is outside {mode} range\"); "
                )
            elif mode == "int64" and not integer_expression:
                normalize = (
                    f"if (computed != trunc(computed) || computed < -9007199254740991.0 || "
                    f"computed > 9007199254740991.0) "
                    f"fail_at({line}, \"result is not an exactly representable int64 value\"); "
                )
            expression_type = "int64_t" if integer_expression else "double"
            format_string = (
                "%lld"
                if integer_expression
                else f"%.{self.program.output_precision}g"
            )
            expression = _c_expression(
                source_expression,
                context,
                line,
                self.variable_indices,
                self.tape_indices,
                integer_mode=integer_expression,
                array_indices=self.array_indices,
                array_variable_indices=self.array_variable_indices,
                user_functions={
                    name: len(tuple(function.data["parameters"]))
                    for name, function in self.functions.items()
                },
            )
            return [
                f"{{ {expression_type} raw = ({expression_type})({expression}); double computed = (double)raw; char number[128];",
                f"  if (!isfinite(computed)) fail_at({line}, \"invalid math result\"); {normalize}",
                f"  snprintf(number, sizeof(number), \"{format_string}\", {'(long long)raw' if integer_expression else 'computed'});",
                f"  set_result(&{result}, STATUS_OK, number); cosmic_release_arrays(); }}",
            ]
        if kind == "while":
            condition = _c_expression(
                str(statement.data["condition"]),
                context,
                line,
                self.variable_indices,
                self.tape_indices,
                array_indices=self.array_indices,
                user_functions={
                    name: len(tuple(function.data["parameters"]))
                    for name, function in self.functions.items()
                },
            )
            if self.max_loop_iterations is None:
                lines = [f"while ({condition}) {{"]
            else:
                lines = [
                    "{",
                    "    unsigned long long _iterations = 0;",
                    f"    while ({condition}) {{",
                    f"        if (_iterations++ >= {self.max_loop_iterations}ULL) fail_at({line}, \"loop iteration limit exceeded\");",
                ]
            for child in statement.body:
                lines.extend(
                    ("    " if self.max_loop_iterations is None else "        ") + generated
                    for generated in self._generate_statement(
                        child,
                        context=context,
                        result=result,
                        block_mode=block_mode,
                        inherit=False,
                    )
                )
            if self.max_loop_iterations is None:
                lines.append("}")
            else:
                lines.extend(["    }", "}"])
            lines.append(f"set_result(&{result}, STATUS_OK, NULL);")
            return lines
        if kind == "if_expr":
            condition = _c_expression(
                str(statement.data["condition"]),
                context,
                line,
                self.variable_indices,
                self.tape_indices,
                array_indices=self.array_indices,
                user_functions={
                    name: len(tuple(function.data["parameters"]))
                    for name, function in self.functions.items()
                },
            )
            body = list(statement.body)
            else_index = next(
                (index for index, child in enumerate(body) if child.kind == "else_marker"),
                len(body),
            )
            lines = [f"if ({condition}) {{"]
            for child in body[:else_index]:
                lines.extend(
                    "    " + generated
                    for generated in self._generate_statement(
                        child,
                        context=context,
                        result=result,
                        block_mode=block_mode,
                        inherit=False,
                    )
                )
            if else_index < len(body):
                lines.append("} else {")
                for child in body[else_index + 1 :]:
                    lines.extend(
                        "    " + generated
                        for generated in self._generate_statement(
                            child,
                            context=context,
                            result=result,
                            block_mode=block_mode,
                            inherit=False,
                        )
                    )
            lines.extend(["}", f"set_result(&{result}, STATUS_OK, NULL);"])
            return lines
        if kind == "tape_create":
            return [
                f"tape_create({self.tape_indices[str(statement.data['name'])]});",
                f"set_result(&{result}, STATUS_OK, \"0\");",
            ]
        if kind == "tape_move":
            direction = -1 if statement.data["direction"] == "left" else 1
            return [
                f"tape_move({self.tape_indices[str(statement.data['name'])]}, {direction}, {line});",
                f"set_result(&{result}, STATUS_OK, NULL);",
            ]
        if kind == "tape_write":
            return [
                f"tape_write_cell({self.tape_indices[str(statement.data['name'])]}, {statement.data['symbol']}, {line});",
                f"set_result(&{result}, STATUS_OK, \"{statement.data['symbol']}\");",
            ]
        if kind == "tape_read":
            return [
                f"{{ char symbol[32]; snprintf(symbol, sizeof(symbol), \"%d\", tape_read_cell({self.tape_indices[str(statement.data['name'])]}, {line}));",
                f"  set_result(&{result}, STATUS_OK, symbol); }}",
            ]
        if kind in {"lock", "n_bomb", "neutron", "proton", "nuclear_reactor"}:
            return self._generate_atom(statement, context, result, block_mode)
        if kind == "bytes_set":
            arguments = tuple(str(item) for item in statement.data["arguments"])
            name = arguments[0].strip("{} ").strip()
            if name not in self.array_indices:
                raise ParseError(line, f"unknown bytes array '{name}'")
            index = _c_expression(arguments[1], context, line, self.variable_indices,
                                  self.tape_indices, integer_mode=True,
                                  array_indices=self.array_indices,
                                  array_variable_indices=self.array_variable_indices)
            value = _c_expression(arguments[2], context, line, self.variable_indices,
                                  self.tape_indices, integer_mode=True,
                                  array_indices=self.array_indices,
                                  array_variable_indices=self.array_variable_indices)
            return [
                f"cosmic_bytes_set({context}, {self.array_indices[name]}, "
                f"(double)({index}), (double)({value}), {line});",
                f"set_result(&{result}, STATUS_OK, NULL);",
            ]
        if kind in {"bytes_get", "bytes_len"}:
            arguments = tuple(str(item) for item in statement.data["arguments"])
            name = arguments[0].strip("{} ").strip()
            if name not in self.array_indices:
                raise ParseError(line, f"unknown bytes array '{name}'")
            index = _c_expression(arguments[1], context, line, self.variable_indices,
                                  self.tape_indices, integer_mode=True,
                                  array_indices=self.array_indices,
                                  array_variable_indices=self.array_variable_indices)
            if kind == "bytes_len":
                return [
                    "{ char text[32];",
                    f"  snprintf(text, sizeof(text), \"%ld\", (long)cosmic_get_array({context}, {self.array_indices[name]}, {line}).length);",
                    f"  set_result(&{result}, STATUS_OK, text); }}",
                ]
            return [
                "{ char text[32];",
                f"  snprintf(text, sizeof(text), \"%lld\", (long long)cosmic_bytes_get({context}, {self.array_indices[name]}, (double)({index}), {line}));",
                f"  set_result(&{result}, STATUS_OK, text); }}",
            ]
        if kind == "calldll":
            self.uses_dll = True
            return self._generate_calldll(statement, context, result)
        if kind == "cdll":
            self.uses_dll = True
            return self._generate_cdll(statement, context, result)
        if kind == "exit":
            return [
                f"cosmic_release_arrays();",
                f"free_tapes();",
                f"fflush(stdout);",
                f"fflush(stderr);",
                f"exit({int(statement.data['code'])});",
            ]
        if kind == "sleep":
            milliseconds = int(float(statement.data["seconds"]) * 1000)
            return [
                f"sleep_ms({milliseconds});",
                f"set_result(&{result}, STATUS_OK, NULL);",
            ]
        if kind == "print":
            return self._generate_print(statement, context, result)
        if kind == "kill":
            index = self.variable_indices[str(statement.data["name"])]
            return [
                f"if (!{context}->vars[{index}].initialized) set_result(&{result}, STATUS_ERROR, \"error\");",
                f"else {{ {context}->vars[{index}].killed = 1; set_result(&{result}, STATUS_OK, \"killer\"); }}",
            ]
        if kind == "if":
            return self._generate_if(statement, context, result, block_mode)
        if kind == "command":
            if block_mode is None:
                raise ParseError(line, "external command assignment must be inside an execution block")
            index = self.variable_indices[str(statement.data["name"])]
            command = _c_string(str(statement.data["command"]))
            return [
                f"{{ int command_status = 0; char *command_output = run_command({command}, &command_status, {line});",
                f"  set_variable({context}, {index}, command_output, \"UTF-8\", {line});",
                f"  if (command_status != 0) fprintf(stderr, \"line {line}: external command exited with status %d\\n\", command_status);",
                f"  set_result(&{result}, command_status == 0 ? STATUS_OK : STATUS_ERROR, command_output);",
                "  free(command_output); }",
            ]
        raise ParseError(line, f"cannot compile unsupported statement '{kind}'")

    def _target_expression(self, spec: object, context: str, result: str, line: int) -> str:
        if not isinstance(spec, dict):
            raise ParseError(line, "invalid assignment value")
        if spec.get("kind") == "literal":
            return _c_string(str(spec.get("value", "")))
        if spec.get("kind") == "up":
            return f"{result}.value"
        raise ParseError(line, "unsupported assignment value")

    def _atom_index(self, name: str, line: int) -> int:
        for block in self.blocks:
            if block.statement.data["name"] == name:
                return block.index
        raise ParseError(line, f"unknown execution block '{name}'")

    def _generate_atom(
        self,
        statement: Statement,
        context: str,
        result: str,
        block_mode: str | None,
    ) -> list[str]:
        self.uses_atom = True
        line = statement.line
        kind = statement.kind
        if kind == "neutron":
            parent = self._atom_index(str(statement.data["parent"]), line)
            child = self._atom_index(str(statement.data["child"]), line)
            return [
                "cosmic_atom_init();",
                f"cosmic_atoms[{child}].parent = {parent};",
                f"set_result(&{result}, STATUS_OK, {_c_string(str(statement.data['child']))});",
            ]
        if kind == "proton":
            return [
                f"set_result(&{result}, STATUS_OK, {_c_string(str(statement.data['chain']))});",
            ]
        if kind == "n_bomb":
            target = str(statement.data["target"])
            if bool(statement.data.get("all")):
                return [
                    "cosmic_atom_init();",
                    "for (int atom = 0; atom < BLOCK_COUNT; ++atom) {",
                    "    cosmic_atoms[atom].stop = 1;",
                    "    cosmic_mutex_lock(&cosmic_atoms[atom].state_lock);",
                    "    cosmic_atoms[atom].state = ATOM_STOPPED;",
                    "    cosmic_mutex_unlock(&cosmic_atoms[atom].state_lock);",
                    "}",
                    'set_result(&result, STATUS_OK, "all");',
                ]
            index = self._atom_index(target, line)
            return [
                "cosmic_atom_init();",
                f"cosmic_atoms[{index}].stop = 1;",
                f"cosmic_mutex_lock(&cosmic_atoms[{index}].state_lock);",
                f"cosmic_atoms[{index}].state = ATOM_STOPPED;",
                f"cosmic_mutex_unlock(&cosmic_atoms[{index}].state_lock);",
                f"set_result(&{result}, STATUS_OK, {_c_string(target)});",
            ]
        if kind == "lock":
            name = str(statement.data["name"])
            index = self._atom_index(name, line)
            timeout = statement.data.get("timeout")
            if timeout is None:
                lines = [
                    "cosmic_atom_init();",
                    f"cosmic_mutex_lock(&cosmic_atoms[{index}].lock);",
                ]
            else:
                milliseconds = max(1, int(float(timeout) * 1000))
                lines = [
                    "cosmic_atom_init();",
                    "{",
                    f"    int lock_acquired_{line} = 0;",
                    f"    for (int lock_wait_{line} = 0; lock_wait_{line} < {milliseconds}; ++lock_wait_{line}) {{",
                    f"        if (cosmic_mutex_trylock(&cosmic_atoms[{index}].lock)) {{ lock_acquired_{line} = 1; break; }}",
                    "        cosmic_thread_sleep(1);",
                    "    }",
                    f"    if (!lock_acquired_{line}) {{",
                    "        ++cosmic_error_count;",
                    f"        fprintf(stderr, \"line {line}: lock timeout: execution block '{name}' is still locked\\n\");",
                    f"        set_result(&{result}, STATUS_ERROR, \"error\");",
                    "    } else {",
                ]
            body_lines: list[str] = []
            for child in statement.body:
                body_lines.extend(
                    self._generate_statement(
                        child,
                        context=context,
                        result=result,
                        block_mode=block_mode,
                        inherit=False,
                    )
                )
            if timeout is None:
                lines.append(f"cosmic_current_stop = &cosmic_atoms[{index}].stop;")
                lines.extend(body_lines)
                lines.extend(
                    [
                        "cosmic_current_stop = NULL;",
                        f"cosmic_mutex_unlock(&cosmic_atoms[{index}].lock);",
                        f"set_result(&{result}, STATUS_OK, NULL);",
                    ]
                )
            else:
                lines.extend("        " + item for item in body_lines)
                lines.extend(
                    [
                        "        cosmic_current_stop = NULL;",
                        f"        cosmic_mutex_unlock(&cosmic_atoms[{index}].lock);",
                        f"        set_result(&{result}, STATUS_OK, NULL);",
                        "    }",
                        "}",
                    ]
                )
            return lines
        chain = str(statement.data["chain"])
        parent_name = str(statement.data["parent"])
        blocks = self.atom_chains.get(chain)
        if blocks is None:
            raise ParseError(line, f"unknown execution chain '{chain}'")
        parent_index = (
            self._atom_index(parent_name, line)
            if any(block.statement.data["name"] == parent_name for block in self.blocks)
            else None
        )
        unique_blocks = list(dict.fromkeys(blocks))
        shared = f"cosmic_chain_context_{line}"
        lines = [
            "cosmic_atom_init();",
            "{",
            f"    Context *{shared} = (Context *)calloc(1, sizeof(Context));",
            f"    if (!{shared}) fail_at({line}, \"out of memory\");",
            f"    inherit_context({shared}, {context});",
        ]
        for block_name in blocks:
            index = self._atom_index(block_name, line)
            lines.extend(
                "    " + item
                for item in self._generate_atom_start(
                    index,
                    parent_index,
                    shared,
                    wait=True,
                    shared_context=True,
                )
            )
        lines.extend(
            [
                f"    inherit_context({context}, {shared});",
                f"    free_context({shared});",
                f"    free({shared});",
                "}",
                f"set_result(&{result}, STATUS_OK, {_c_string(chain)});",
            ]
        )
        return lines

    def _generate_atom_start(
        self,
        index: int,
        parent_index: int | None,
        context: str,
        wait: bool = False,
        shared_context: bool = False,
    ) -> list[str]:
        block = self.blocks[index]
        return [
            f"if (cosmic_atoms[{index}].has_thread) {{ cosmic_thread_join(cosmic_atoms[{index}].thread); cosmic_atoms[{index}].has_thread = 0; }}",
            f"cosmic_atoms[{index}].runner = {block.c_name};",
            f"cosmic_atoms[{index}].stop = 0;",
            f"cosmic_atoms[{index}].state = ATOM_RUNNING;",
            (
                f"cosmic_atoms[{index}].context = {context};\n"
                f"cosmic_atoms[{index}].has_context = 1;\n"
                f"cosmic_atoms[{index}].shared = 1;"
                if shared_context
                else (
                    f"if (!cosmic_atoms[{index}].has_context) {{\n"
                    f"    Context *parent_copy = (Context *)calloc(1, sizeof(Context));\n"
                    f"    if (!parent_copy) fail_at({block.statement.line}, \"out of memory\");\n"
                    f"    inherit_context(parent_copy, {context});\n"
                    f"    cosmic_atoms[{index}].context = parent_copy;\n"
                    f"    cosmic_atoms[{index}].has_context = 1;\n"
                    f"    cosmic_atoms[{index}].shared = 0;\n"
                    f"}}"
                )
            ),
            f"{{",
            f"    cosmic_thread thread;",
            f"    if (cosmic_thread_create(&thread, cosmic_atom_thread_{index}, &cosmic_atoms[{index}]) != COSMIC_THREAD_OK)",
            f"        fail_at({block.statement.line}, \"cannot start atom thread\");",
            f"    cosmic_atoms[{index}].thread = thread;",
            f"    cosmic_atoms[{index}].has_thread = 1;",
            f"}}",
            *(
                [
                    f"cosmic_thread_join(cosmic_atoms[{index}].thread);",
                    f"cosmic_atoms[{index}].has_thread = 0;",
                ]
                if wait
                else []
            ),
        ]

    def _generate_dll_declarations(self) -> list[str]:
        lines = ["/* C DLL declarations */"]
        for name, parameters, return_type, _symbol, convention in self.program.extern_functions:
            parameter_text = ", ".join(
                _DLL_C_TYPES.get(parameter, "void *") for parameter in parameters
            ) or "void"
            calling = "__stdcall" if convention == "stdcall" else ""
            lines.append(
                "typedef {ret} ({calling} *cosmic_fn_{name})({params});".format(
                    ret=_DLL_C_TYPES.get(return_type, "long long"),
                    calling=calling,
                    name=name,
                    params=parameter_text,
                )
            )
        if self.program.link_libraries:
            lines.append("/* link libraries: {} */".format(", ".join(self.program.link_libraries)))
        lines.append("")
        return lines

    def _generate_cdll(self, statement: Statement, context: str, result: str) -> list[str]:
        line = statement.line
        operation = str(statement.data["operation"])
        library = _c_string(str(statement.data["library"]))
        if operation == "status":
            return [
                f"set_result(&{result}, STATUS_OK, cosmic_dll_status({library}));",
            ]
        if operation == "out":
            return [
                f"if (!cosmic_dll_unload({library})) {{ ++cosmic_error_count;",
                f"  fprintf(stderr, \"line {line}: DLL is not loaded\\n\");",
                f"  set_result(&{result}, STATUS_ERROR, \"unloaded\"); }}",
                f"else set_result(&{result}, STATUS_OK, \"unloaded\");",
            ]
        function = str(statement.data["function"])
        arguments = statement.data.get("arguments", ())
        if not isinstance(arguments, tuple):
            raise ParseError(line, "invalid cdll arguments")
        declaration = next(
            (
                item
                for item in self.program.extern_functions
                if item[0] == function
            ),
            None,
        )
        if declaration is None:
            raise ParseError(line, f"DLL function '{function}' has no #extern declaration")
        _name, parameters, return_type, export_symbol, _convention = declaration
        converted = []
        for index, argument in enumerate(arguments):
            rendered = _c_expression(
                str(argument),
                context,
                line,
                self.variable_indices,
                self.tape_indices,
                array_indices=self.array_indices,
                array_variable_indices=self.array_variable_indices,
            )
            declared = parameters[index] if index < len(parameters) else None
            converted.append(_c_cast_dll_argument(rendered, declared))
        if return_type == "VOID":
            call = (
                f"((cosmic_fn_{function})symbol)({', '.join(converted)})"
                if converted
                else f"((cosmic_fn_{function})symbol)()"
            )
            return [
                f"{{ #ifdef _WIN32",
                f"  void *symbol = cosmic_dll_symbol({library}, {_c_string(export_symbol)});",
                f"  #else",
                f"  void *symbol = cosmic_dll_symbol({library}, {_c_string(export_symbol)});",
                f"  #endif",
                f"  if (!symbol) {{ ++cosmic_error_count;",
                f"    fprintf(stderr, \"line {line}: DLL function is not available\\n\");",
                f"    set_result(&{result}, STATUS_ERROR, \"error\"); }}",
                f"  else {{ {call}; set_result(&{result}, STATUS_OK, \"ok\"); }} }}",
            ]
        numeric = _DLL_NUMERIC_RETURNS.get(return_type, "long long")
        format_string = _DLL_FORMATS.get(return_type, "%lld")
        call = (
            f"((cosmic_fn_{function})symbol)({', '.join(converted)})"
            if converted
            else f"((cosmic_fn_{function})symbol)()"
        )
        return [
            f"{{ void *symbol = cosmic_dll_symbol({library}, {_c_string(export_symbol)});",
            f"  if (!symbol) {{ ++cosmic_error_count;",
            f"    fprintf(stderr, \"line {line}: DLL function is not available\\n\");",
            f"    set_result(&{result}, STATUS_ERROR, \"error\"); }}",
            f"  else {{ {numeric} value = ({numeric}){call}; char text[64];",
            f"    snprintf(text, sizeof(text), \"{format_string}\", value);",
            f"    set_result(&{result}, STATUS_OK, text); }} }}",
        ]

    def _generate_calldll(self, statement: Statement, context: str, result: str) -> list[str]:
        line = statement.line
        library = str(statement.data["library"])
        return [
            f"if (!cosmic_dll_load({_c_string(library)}, {line})) {{ ++cosmic_error_count;",
            f"  fprintf(stderr, \"line {line}: cannot load DLL\\n\");",
            f"  set_result(&{result}, STATUS_ERROR, \"missing\"); }}",
            f"else set_result(&{result}, STATUS_OK, {_c_string(library)});",
        ]

    def _generate_print(self, statement: Statement, context: str, result: str) -> list[str]:
        line = statement.line
        expression = str(statement.data["expression"]).strip()
        encoding = statement.data.get("encoding")
        value_spec = statement.data.get("value")
        if isinstance(value_spec, dict) and value_spec.get("kind") == "variable":
            index = self.variable_indices[str(value_spec.get("name", ""))]
            return [
                f"if (!{context}->vars[{index}].initialized) {{ ++cosmic_error_count; set_result(&{result}, STATUS_ERROR, \"error\"); }}",
                f"else if ({context}->vars[{index}].killed) {{ ++cosmic_error_count; set_result(&{result}, STATUS_ERROR, \"killer\"); }}",
                "else {",
                f"    char *printed_value = get_variable({context}, {index}, {line});",
                f"    print_value(printed_value, {_c_string(str(encoding)) if encoding else f'{context}->vars[{index}].encoding'});",
                f"    set_result(&{result}, STATUS_OK, printed_value); free(printed_value);",
                "}",
            ]
        if isinstance(value_spec, dict) and value_spec.get("kind") == "literal":
            value = str(value_spec.get("value", ""))
            return [
                f"print_value({_c_string(value)}, {_c_string(str(encoding)) if encoding else 'NULL'});",
                f"set_result(&{result}, STATUS_OK, {_c_string(value)});",
            ]
        if isinstance(value_spec, dict) and value_spec.get("kind") == "up":
            return [
                f"if (!{result}.value) fail_at({line}, \"up has no value\");",
                f"print_value({result}.value, {_c_string(str(encoding)) if encoding else 'NULL'});",
                f"set_result(&{result}, STATUS_OK, {result}.value);",
            ]
        if isinstance(value_spec, dict) and value_spec.get("kind") == "concat":
            parts = value_spec.get("parts")
            if not isinstance(parts, list) or not parts:
                raise ParseError(line, "invalid string concatenation")
            expressions: list[str] = []
            for part in parts:
                if not isinstance(part, dict):
                    raise ParseError(line, "invalid string concatenation part")
                if part.get("kind") == "literal":
                    expressions.append(f"copy_text({_c_string(str(part.get('value', '')))})")
                elif part.get("kind") == "variable":
                    variable_name = str(part.get("name", ""))
                    if variable_name not in self.variable_indices:
                        raise ParseError(line, f"unknown variable '{variable_name}'")
                    expressions.append(
                        f"get_variable({context}, {self.variable_indices[variable_name]}, {line})"
                    )
                elif part.get("kind") == "up":
                    expressions.append(f"copy_text({result}.value)")
                else:
                    raise ParseError(line, "unsupported string concatenation part")
            parts_array = ", ".join(expressions)
            encoding_expr = _c_string(str(encoding)) if encoding else "NULL"
            checks = [
                f"if (!parts[{part_index}]) fail_at({line}, \"variable '{part['name']}' is not declared or is empty\");"
                for part_index, part in enumerate(parts)
                if isinstance(part, dict) and part.get("kind") == "variable"
            ]
            checks.extend(
                f"if (!parts[{part_index}]) fail_at({line}, \"up has no value\");"
                for part_index, part in enumerate(parts)
                if isinstance(part, dict) and part.get("kind") == "up"
            )
            return [
                f"{{ char *parts[] = {{{parts_array}}};",
                *checks,
                f"  char *joined = concat_texts(parts, {len(expressions)}, {line});",
                f"  print_value(joined, {encoding_expr});",
                f"  set_result(&{result}, STATUS_OK, joined); free(joined); }}",
            ]
        return [
            f"print_value({_c_string(expression)}, {_c_string(str(encoding)) if encoding else 'NULL'});",
            f"set_result(&{result}, STATUS_OK, {_c_string(expression)});",
        ]

    def _generate_if(
        self,
        statement: Statement,
        context: str,
        result: str,
        block_mode: str | None,
    ) -> list[str]:
        line = statement.line
        selector = str(statement.data["selector"])
        expected = _c_string(str(statement.data["expected"]))
        if selector == "status":
            actual = f"({result}.status == STATUS_OK ? \"ok\" : {result}.status == STATUS_STOP ? \"stop\" : \"error\")"
        elif selector == "*":
            actual = f"{result}.value"
        else:
            index = self.variable_indices[selector]
            actual = None
        target = statement.data["target"]
        if not isinstance(target, dict):
            raise ParseError(line, "invalid goto target")
        if actual is None:
            condition = f"variable_matches({context}, {index}, {expected}, {line})"
        else:
            condition = f"({actual} && _stricmp({actual}, {expected}) == 0)"
        lines = [f"if ({condition}) {{"]
        if target.get("kind") == "stop":
            if block_mode is None:
                raise ParseError(line, "goto stop is only valid inside an execution block")
            lines.extend(
                [
                    f"        set_result(&{result}, STATUS_STOP, NULL);",
                    "        stopped = 1;",
                    "        goto block_primary_done;",
                ]
            )
        elif target.get("kind") == "push":
            index = self.variable_indices[str(target["name"])]
            encoding = target.get("encoding")
            encoding_expr = _c_string(str(encoding)) if encoding else f"{context}->vars[{index}].encoding"
            value_spec = target["value"]
            if isinstance(value_spec, dict) and value_spec.get("kind") == "variable":
                source_name = str(value_spec.get("name", ""))
                source_index = self.variable_indices[source_name]
                lines.extend(
                    [
                        f"        char *source_value = get_variable({context}, {source_index}, {line});",
                        f"        if (!source_value) fail_at({line}, \"variable '{source_name}' is not declared or is empty\");",
                        f"        set_variable({context}, {index}, source_value, {encoding_expr}, {line});",
                        f"        set_result(&{result}, STATUS_OK, source_value); free(source_value);",
                    ]
                )
            else:
                value_expr = self._target_expression(value_spec, context, result, line)
                lines.extend(
                    [
                        f"        set_variable({context}, {index}, {value_expr}, {encoding_expr}, {line});",
                        f"        set_result(&{result}, STATUS_OK, {value_expr});",
                    ]
                )
        else:
            raise ParseError(line, "unsupported goto target")
        lines.extend(
            [
                "} else {",
                f"        set_result(&{result}, STATUS_OK, NULL);",
                "}",
            ]
        )
        return lines


def generate_c(program: Program, *, max_loop_iterations: int | None = None) -> str:
    return CGenerator(program, max_loop_iterations=max_loop_iterations).generate()


def write_c(
    program: Program,
    destination: Path,
    *,
    max_loop_iterations: int | None = None,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        generate_c(program, max_loop_iterations=max_loop_iterations),
        encoding="utf-8",
    )
    return destination
