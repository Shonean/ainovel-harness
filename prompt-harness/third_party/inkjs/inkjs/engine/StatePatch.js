"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.StatePatch = void 0;
class StatePatch {
    get globals() {
        return this._globals;
    }
    get changedVariables() {
        return this._changedVariables;
    }
    get visitCounts() {
        return this._visitCounts;
    }
    get turnIndices() {
        return this._turnIndices;
    }
    constructor() {
        this._changedVariables = new Set();
        this._visitCounts = new Map();
        this._turnIndices = new Map();
        if (arguments.length === 1 && arguments[0] !== null) {
            let toCopy = arguments[0];
            this._globals = new Map(toCopy._globals);
            this._changedVariables = new Set(toCopy._changedVariables);
            this._visitCounts = new Map(toCopy._visitCounts);
            this._turnIndices = new Map(toCopy._turnIndices);
        }
        else {
            this._globals = new Map();
            this._changedVariables = new Set();
            this._visitCounts = new Map();
            this._turnIndices = new Map();
        }
    }
    TryGetGlobal(name, /* out */ value) {
        if (name !== null && this._globals.has(name)) {
            return { result: this._globals.get(name), exists: true };
        }
        return { result: value, exists: false };
    }
    SetGlobal(name, value) {
        this._globals.set(name, value);
    }
    AddChangedVariable(name) {
        return this._changedVariables.add(name);
    }
    TryGetVisitCount(container, /* out */ count) {
        if (this._visitCounts.has(container)) {
            return { result: this._visitCounts.get(container), exists: true };
        }
        return { result: count, exists: false };
    }
    SetVisitCount(container, count) {
        this._visitCounts.set(container, count);
    }
    SetTurnIndex(container, index) {
        this._turnIndices.set(container, index);
    }
    TryGetTurnIndex(container, /* out */ index) {
        if (this._turnIndices.has(container)) {
            return { result: this._turnIndices.get(container), exists: true };
        }
        return { result: index, exists: false };
    }
}
exports.StatePatch = StatePatch;
//# sourceMappingURL=StatePatch.js.map