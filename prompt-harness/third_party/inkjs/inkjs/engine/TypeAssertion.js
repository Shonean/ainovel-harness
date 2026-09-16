"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.filterUndef = exports.isEquatable = exports.nullIfUndefined = exports.asINamedContentOrNull = exports.asBooleanOrThrows = exports.asNumberOrThrows = exports.asOrThrows = exports.asOrNull = void 0;
function asOrNull(obj, type) {
    if (obj instanceof type) {
        return unsafeTypeAssertion(obj, type);
    }
    else {
        return null;
    }
}
exports.asOrNull = asOrNull;
function asOrThrows(obj, type) {
    if (obj instanceof type) {
        return unsafeTypeAssertion(obj, type);
    }
    else {
        throw new Error(`${obj} is not of type ${type}`);
    }
}
exports.asOrThrows = asOrThrows;
function asNumberOrThrows(obj) {
    if (typeof obj === "number") {
        return obj;
    }
    else {
        throw new Error(`${obj} is not a number`);
    }
}
exports.asNumberOrThrows = asNumberOrThrows;
function asBooleanOrThrows(obj) {
    if (typeof obj === "boolean") {
        return obj;
    }
    else {
        throw new Error(`${obj} is not a boolean`);
    }
}
exports.asBooleanOrThrows = asBooleanOrThrows;
// So here, in the reference implementation, contentObj is casted to an INamedContent
// but here we use js-style duck typing: if it implements the same props as the interface,
// we treat it as valid.
function asINamedContentOrNull(obj) {
    if (obj.hasValidName && obj.name) {
        return obj;
    }
    return null;
}
exports.asINamedContentOrNull = asINamedContentOrNull;
function nullIfUndefined(obj) {
    if (typeof obj === "undefined") {
        return null;
    }
    return obj;
}
exports.nullIfUndefined = nullIfUndefined;
function isEquatable(type) {
    return typeof type === "object" && typeof type.Equals === "function";
}
exports.isEquatable = isEquatable;
function unsafeTypeAssertion(obj, 
// eslint-disable-next-line @typescript-eslint/no-unused-vars
type) {
    return obj;
}
function filterUndef(element) {
    return element != undefined;
}
exports.filterUndef = filterUndef;
//# sourceMappingURL=TypeAssertion.js.map