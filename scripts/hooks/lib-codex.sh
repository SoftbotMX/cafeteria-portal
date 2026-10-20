# Lo que comparten el hook y `codex-visto`. No se ejecuta solo.

# Huella del diff que está staged. Devuelve 1 si no se puede calcular: quien
# llame DEBE fallar cerrado, nunca seguir con una huella vacía.
huella_del_staged() {
    local salida hash
    salida="$(git diff --cached | shasum -a 256)" || return 1
    hash="${salida%% *}"
    # sha256 en hexadecimal son 64 caracteres. Cualquier otra cosa es un fallo
    # disfrazado.
    [ "${#hash}" -eq 64 ] || return 1
    printf '%s' "$hash"
}

# ¿Es documentación? Entonces Codex «rinde poco» (AGENTS.md) y no se exige.
#
# 🔴 Ojo con lo que NO entra aquí: `.csv` y `.txt` se quedan FUERA de la lista
# a propósito. En este repo `security/ir.model.access.csv` son los permisos y
# los catálogos fiscales también son CSV; tratarlos como documentación dejaba
# pasar un cambio de ACL sin revisar.
es_documentacion() {
    case "$1" in
        *.md|*.png|*.PNG|*.jpg|*.JPG|*.jpeg|*.gif|*.svg|*.pdf|*.PDF) return 0 ;;
        *) return 1 ;;
    esac
}
