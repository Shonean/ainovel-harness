"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.VariablesState = void 0;
const Value_1 = require("./Value");
const StoryException_1 = require("./StoryException");
const JsonSerialisation_1 = require("./JsonSerialisation");
const TypeAssertion_1 = require("./TypeAssertion");
const TryGetResult_1 = require("./TryGetResult");
const NullException_1 = require("./NullException");
// Fake class wrapper around VariableState to have correct typing
// when using the Proxy syntax in typescript
function VariablesStateAccessor() {
    return class {
    };
}
class VariablesState extends VariablesStateAccessor() {
    variableChangedEvent(variableName, newValue) {
        for (let callback of this.variableChangedEventCallbacks) {
            callback(variableName, newValue);
        }
    }
    StartVariableObservation() {
        this._batchObservingVariableChanges = true;
        this._changedVariablesForBatchObs = new Set();
    }
    CompleteVariableObservation() {
        this._batchObservingVariableChanges = false;
        let changedVars = new Map();
        if (this._changedVariablesForBatchObs != null) {
            for (let variableName of this._changedVariablesForBatchObs) {
                let currentValue = this._globalVariables.get(variableName);
                this.variableChangedEvent(variableName, currentValue);
            }
        }
        // Patch may still be active - e.g. if we were in the middle of a background save
        if (this.patch != null) {
            for (let variableName of this.patch.changedVariables) {
                let patchedVal = this.patch.TryGetGlobal(variableName, null);
                if (patchedVal.exists)
                    changedVars.set(variableName, patchedVal);
            }
        }
        this._changedVariablesForBatchObs = null;
        return changedVars;
    }
    NotifyObservers(changedVars) {
        for (const [key, value] of changedVars) {
            this.variableChangedEvent(key, value);
        }
    }
    get callStack() {
        return this._callStack;
    }
    set callStack(callStack) {
        this._callStack = callStack;
    }
    $(variableName, value) {
        if (typeof value === "undefined") {
            let varContents = null;
            if (this.patch !== null) {
                varContents = this.patch.TryGetGlobal(variableName, null);
                if (varContents.exists)
                    return varContents.result.valueObject;
            }
            varContents = this._globalVariables.get(variableName);
            if (typeof varContents === "undefined") {
                varContents = this._defaultGlobalVariables.get(variableName);
            }
            if (typeof varContents !== "undefined")
                return varContents.valueObject;
            else
                return null;
        }
        else {
            if (typeof this._defaultGlobalVariables.get(variableName) === "undefined")
                throw new StoryException_1.StoryException("Cannot assign to a variable (" +
                    variableName +
                    ") that hasn't been declared in the story");
            let val = Value_1.Value.Create(value);
            if (val == null) {
                if (value == null) {
                    throw new Error("Cannot pass null to VariableState");
                }
                else {
                    throw new Error("Invalid value passed to VariableState: " + value.toString());
                }
            }
            this.SetGlobal(variableName, val);
        }
    }
    constructor(callStack, listDefsOrigin) {
        super();
        // The way variableChangedEvent is a bit different than the reference implementation.
        // Originally it uses the C# += operator to add delegates, but in js we need to maintain
        // an actual collection of delegates (ie. callbacks) to register a new one, there is a
        // special ObserveVariableChange method below.
        this.variableChangedEventCallbacks = [];
        this.patch = null;
        this._defaultGlobalVariables = new Map();
        this._changedVariablesForBatchObs = new Set();
        this._batchObservingVariableChanges = false;
        this._globalVariables = new Map();
        this._callStack = callStack;
        this._listDefsOrigin = listDefsOrigin;
        // if es6 proxies are available, use them.
        try {
            // the proxy is used to allow direct manipulation of global variables.
            // It first tries to access the objects own property, and if none is
            // found it delegates the call to the $ method, defined below
            let p = new Proxy(this, {
                get(target, name) {
                    return name in target ? target[name] : target.$(name);
                },
                set(target, name, value) {
                    if (name in target)
                        target[name] = value;
                    else
                        target.$(name, value);
                    return true; // returning a falsy value make the trap fail
                },
                ownKeys(target) {
                    return [
                        ...new Set([
                            ...target._defaultGlobalVariables.keys(),
                            ...target._globalVariables.keys(),
                        ]),
                    ];
                },
                getOwnPropertyDescriptor(target, name) {
                    // called for every property
                    return {
                        enumerable: true,
                        configurable: true,
                        value: target.$(name),
                    };
                },
            });
            return p;
        }
        catch (e) {
            // the proxy object is not available in this context. we should warn the
            // dev but writing to the console feels a bit intrusive.
            // console.log("ES6 Proxy not available - direct manipulation of global variables can't work, use $() instead.");
        }
    }
    ApplyPatch() {
        if (this.patch === null) {
            return (0, NullException_1.throwNullException)("this.patch");
        }
        for (let [namedVarKey, namedVarValue] of this.patch.globals) {
            this._globalVariables.set(namedVarKey, namedVarValue);
        }
        if (this._changedVariablesForBatchObs !== null) {
            for (let name of this.patch.changedVariables) {
                this._changedVariablesForBatchObs.add(name);
            }
        }
        this.patch = null;
    }
    SetJsonToken(jToken) {
        this._globalVariables.clear();
        for (let [varValKey, varValValue] of this._defaultGlobalVariables) {
            let loadedToken = jToken[varValKey];
            if (typeof loadedToken !== "undefined") {
                let tokenInkObject = JsonSerialisation_1.JsonSerialisation.JTokenToRuntimeObject(loadedToken);
                if (tokenInkObject === null) {
                    return (0, NullException_1.throwNullException)("tokenInkObject");
                }
                this._globalVariables.set(varValKey, tokenInkObject);
            }
            else {
                this._globalVariables.set(varValKey, varValValue);
            }
        }
    }
    WriteJson(writer) {
        writer.WriteObjectStart();
        for (let [keyValKey, keyValValue] of this._globalVariables) {
            let name = keyValKey;
            let val = keyValValue;
            if (VariablesState.dontSaveDefaultValues) {
                if (this._defaultGlobalVariables.has(name)) {
                    let defaultVal = this._defaultGlobalVariables.get(name);
                    if (this.RuntimeObjectsEqual(val, defaultVal))
                        continue;
                }
            }
            writer.WritePropertyStart(name);
            JsonSerialisation_1.JsonSerialisation.WriteRuntimeObject(writer, val);
            writer.WritePropertyEnd();
        }
        writer.WriteObjectEnd();
    }
    RuntimeObjectsEqual(obj1, obj2) {
        if (obj1 === null) {
            return (0, NullException_1.throwNullException)("obj1");
        }
        if (obj2 === null) {
            return (0, NullException_1.throwNullException)("obj2");
        }
        if (obj1.constructor !== obj2.constructor)
            return false;
        let boolVal = (0, TypeAssertion_1.asOrNull)(obj1, Value_1.BoolValue);
        if (boolVal !== null) {
            return boolVal.value === (0, TypeAssertion_1.asOrThrows)(obj2, Value_1.BoolValue).value;
        }
        let intVal = (0, TypeAssertion_1.asOrNull)(obj1, Value_1.IntValue);
        if (intVal !== null) {
            return intVal.value === (0, TypeAssertion_1.asOrThrows)(obj2, Value_1.IntValue).value;
        }
        let floatVal = (0, TypeAssertion_1.asOrNull)(obj1, Value_1.FloatValue);
        if (floatVal !== null) {
            return floatVal.value === (0, TypeAssertion_1.asOrThrows)(obj2, Value_1.FloatValue).value;
        }
        let val1 = (0, TypeAssertion_1.asOrNull)(obj1, Value_1.Value);
        let val2 = (0, TypeAssertion_1.asOrNull)(obj2, Value_1.Value);
        if (val1 !== null && val2 !== null) {
            if ((0, TypeAssertion_1.isEquatable)(val1.valueObject) && (0, TypeAssertion_1.isEquatable)(val2.valueObject)) {
                return val1.valueObject.Equals(val2.valueObject);
            }
            else {
                return val1.valueObject === val2.valueObject;
            }
        }
        throw new Error("FastRoughDefinitelyEquals: Unsupported runtime object type: " +
            obj1.constructor.name);
    }
    GetVariableWithName(name, contextIndex = -1) {
        let varValue = this.GetRawVariableWithName(name, contextIndex);
        // var varPointer = varValue as VariablePointerValue;
        let varPointer = (0, TypeAssertion_1.asOrNull)(varValue, Value_1.VariablePointerValue);
        if (varPointer !== null) {
            varValue = this.ValueAtVariablePointer(varPointer);
        }
        return varValue;
    }
    TryGetDefaultVariableValue(name) {
        let val = (0, TryGetResult_1.tryGetValueFromMap)(this._defaultGlobalVariables, name, null);
        return val.exists ? val.result : null;
    }
    GlobalVariableExistsWithName(name) {
        return (this._globalVariables.has(name) ||
            (this._defaultGlobalVariables !== null &&
                this._defaultGlobalVariables.has(name)));
    }
    GetRawVariableWithName(name, contextIndex) {
        let varValue = null;
        if (contextIndex == 0 || contextIndex == -1) {
            let variableValue = null;
            if (this.patch !== null) {
                variableValue = this.patch.TryGetGlobal(name, null);
                if (variableValue.exists)
                    return variableValue.result;
            }
            // this is a conditional assignment
            variableValue = (0, TryGetResult_1.tryGetValueFromMap)(this._globalVariables, name, null);
            if (variableValue.exists)
                return variableValue.result;
            if (this._defaultGlobalVariables !== null) {
                variableValue = (0, TryGetResult_1.tryGetValueFromMap)(this._defaultGlobalVariables, name, null);
                if (variableValue.exists)
                    return variableValue.result;
            }
            if (this._listDefsOrigin === null)
                return (0, NullException_1.throwNullException)("VariablesState._listDefsOrigin");
            let listItemValue = this._listDefsOrigin.FindSingleItemListWithName(name);
            if (listItemValue)
                return listItemValue;
        }
        varValue = this._callStack.GetTemporaryVariableWithName(name, contextIndex);
        return varValue;
    }
    ValueAtVariablePointer(pointer) {
        return this.GetVariableWithName(pointer.variableName, pointer.contextIndex);
    }
    Assign(varAss, value) {
        let name = varAss.variableName;
        if (name === null) {
            return (0, NullException_1.throwNullException)("name");
        }
        let contextIndex = -1;
        let setGlobal = false;
        if (varAss.isNewDeclaration) {
            setGlobal = varAss.isGlobal;
        }
        else {
            setGlobal = this.GlobalVariableExistsWithName(name);
        }
        if (varAss.isNewDeclaration) {
            // var varPointer = value as VariablePointerValue;
            let varPointer = (0, TypeAssertion_1.asOrNull)(value, Value_1.VariablePointerValue);
            if (varPointer !== null) {
                let fullyResolvedVariablePointer = this.ResolveVariablePointer(varPointer);
                value = fullyResolvedVariablePointer;
            }
        }
        else {
            let existingPointer = null;
            do {
                // existingPointer = GetRawVariableWithName (name, contextIndex) as VariablePointerValue;
                existingPointer = (0, TypeAssertion_1.asOrNull)(this.GetRawVariableWithName(name, contextIndex), Value_1.VariablePointerValue);
                if (existingPointer != null) {
                    name = existingPointer.variableName;
                    contextIndex = existingPointer.contextIndex;
                    setGlobal = contextIndex == 0;
                }
            } while (existingPointer != null);
        }
        if (setGlobal) {
            this.SetGlobal(name, value);
        }
        else {
            this._callStack.SetTemporaryVariable(name, value, varAss.isNewDeclaration, contextIndex);
        }
    }
    SnapshotDefaultGlobals() {
        this._defaultGlobalVariables = new Map(this._globalVariables);
    }
    RetainListOriginsForAssignment(oldValue, newValue) {
        let oldList = (0, TypeAssertion_1.asOrThrows)(oldValue, Value_1.ListValue);
        let newList = (0, TypeAssertion_1.asOrThrows)(newValue, Value_1.ListValue);
        if (oldList.value && newList.value && newList.value.Count == 0) {
            newList.value.SetInitialOriginNames(oldList.value.originNames);
        }
    }
    SetGlobal(variableName, value) {
        let oldValue = null;
        if (this.patch === null) {
            oldValue = (0, TryGetResult_1.tryGetValueFromMap)(this._globalVariables, variableName, null);
        }
        if (this.patch !== null) {
            oldValue = this.patch.TryGetGlobal(variableName, null);
            if (!oldValue.exists) {
                oldValue = (0, TryGetResult_1.tryGetValueFromMap)(this._globalVariables, variableName, null);
            }
        }
        Value_1.ListValue.RetainListOriginsForAssignment(oldValue.result, value);
        if (variableName === null) {
            return (0, NullException_1.throwNullException)("variableName");
        }
        if (this.patch !== null) {
            this.patch.SetGlobal(variableName, value);
        }
        else {
            this._globalVariables.set(variableName, value);
        }
        // TODO: Not sure !== is equivalent to !value.Equals(oldValue)
        if (this.variableChangedEvent !== null &&
            oldValue !== null &&
            value !== oldValue.result) {
            if (this._batchObservingVariableChanges) {
                if (this._changedVariablesForBatchObs === null) {
                    return (0, NullException_1.throwNullException)("this._changedVariablesForBatchObs");
                }
                if (this.patch !== null) {
                    this.patch.AddChangedVariable(variableName);
                }
                else if (this._changedVariablesForBatchObs !== null) {
                    this._changedVariablesForBatchObs.add(variableName);
                }
            }
            else {
                this.variableChangedEvent(variableName, value);
            }
        }
    }
    ResolveVariablePointer(varPointer) {
        let contextIndex = varPointer.contextIndex;
        if (contextIndex == -1)
            contextIndex = this.GetContextIndexOfVariableNamed(varPointer.variableName);
        let valueOfVariablePointedTo = this.GetRawVariableWithName(varPointer.variableName, contextIndex);
        // var doubleRedirectionPointer = valueOfVariablePointedTo as VariablePointerValue;
        let doubleRedirectionPointer = (0, TypeAssertion_1.asOrNull)(valueOfVariablePointedTo, Value_1.VariablePointerValue);
        if (doubleRedirectionPointer != null) {
            return doubleRedirectionPointer;
        }
        else {
            return new Value_1.VariablePointerValue(varPointer.variableName, contextIndex);
        }
    }
    GetContextIndexOfVariableNamed(varName) {
        if (this.GlobalVariableExistsWithName(varName))
            return 0;
        return this._callStack.currentElementIndex;
    }
    /**
     * This function is specific to the js version of ink. It allows to register a
     * callback that will be called when a variable changes. The original code uses
     * `state.variableChangedEvent += callback` instead.
     *
     * @param {function} callback
     */
    ObserveVariableChange(callback) {
        this.variableChangedEventCallbacks.push(callback);
    }
}
exports.VariablesState = VariablesState;
VariablesState.dontSaveDefaultValues = true;
//# sourceMappingURL=VariablesState.js.map