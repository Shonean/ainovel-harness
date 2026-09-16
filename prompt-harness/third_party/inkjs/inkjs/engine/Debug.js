"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.Debug = void 0;
var Debug;
(function (Debug) {
    function AssertType(variable, type, message) {
        Assert(variable instanceof type, message);
    }
    Debug.AssertType = AssertType;
    function Assert(condition, message) {
        if (!condition) {
            if (typeof message !== "undefined") {
                console.warn(message);
            }
            if (console.trace) {
                console.trace();
            }
            throw new Error("");
        }
    }
    Debug.Assert = Assert;
})(Debug || (exports.Debug = Debug = {}));
//# sourceMappingURL=Debug.js.map