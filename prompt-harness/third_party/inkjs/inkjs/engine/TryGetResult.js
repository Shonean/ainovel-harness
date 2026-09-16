"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.tryParseFloat = exports.tryParseInt = exports.tryGetValueFromMap = void 0;
function tryGetValueFromMap(map, key, 
/* out */ value) {
    if (map === null) {
        return { result: value, exists: false };
    }
    let val = map.get(key);
    if (typeof val === "undefined") {
        return { result: value, exists: false };
    }
    else {
        return { result: val, exists: true };
    }
}
exports.tryGetValueFromMap = tryGetValueFromMap;
function tryParseInt(value, 
/* out */ defaultValue = 0) {
    let val = parseInt(value);
    if (!Number.isNaN(val)) {
        return { result: val, exists: true };
    }
    else {
        return { result: defaultValue, exists: false };
    }
}
exports.tryParseInt = tryParseInt;
function tryParseFloat(value, 
/* out */ defaultValue = 0) {
    let val = parseFloat(value);
    if (!Number.isNaN(val)) {
        return { result: val, exists: true };
    }
    else {
        return { result: defaultValue, exists: false };
    }
}
exports.tryParseFloat = tryParseFloat;
//# sourceMappingURL=TryGetResult.js.map