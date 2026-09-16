"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.NativeFunctionCall = void 0;
const Value_1 = require("./Value");
const StoryException_1 = require("./StoryException");
const Void_1 = require("./Void");
const InkList_1 = require("./InkList");
const Object_1 = require("./Object");
const TypeAssertion_1 = require("./TypeAssertion");
const NullException_1 = require("./NullException");
class NativeFunctionCall extends Object_1.InkObject {
    static CallWithName(functionName) {
        return new NativeFunctionCall(functionName);
    }
    static CallExistsWithName(functionName) {
        this.GenerateNativeFunctionsIfNecessary();
        return this._nativeFunctions.get(functionName);
    }
    get name() {
        if (this._name === null)
            return (0, NullException_1.throwNullException)("NativeFunctionCall._name");
        return this._name;
    }
    set name(value) {
        this._name = value;
        if (!this._isPrototype) {
            if (NativeFunctionCall._nativeFunctions === null)
                (0, NullException_1.throwNullException)("NativeFunctionCall._nativeFunctions");
            else
                this._prototype =
                    NativeFunctionCall._nativeFunctions.get(this._name) || null;
        }
    }
    get numberOfParameters() {
        if (this._prototype) {
            return this._prototype.numberOfParameters;
        }
        else {
            return this._numberOfParameters;
        }
    }
    set numberOfParameters(value) {
        this._numberOfParameters = value;
    }
    Call(parameters) {
        if (this._prototype) {
            return this._prototype.Call(parameters);
        }
        if (this.numberOfParameters != parameters.length) {
            throw new Error("Unexpected number of parameters");
        }
        let hasList = false;
        for (let p of parameters) {
            if (p instanceof Void_1.Void)
                throw new StoryException_1.StoryException("Attempting to perform " +
                    this.name +
                    ' on a void value. Did you forget to "return" a value from a function you called here?');
            if (p instanceof Value_1.ListValue)
                hasList = true;
        }
        if (parameters.length == 2 && hasList) {
            return this.CallBinaryListOperation(parameters);
        }
        let coercedParams = this.CoerceValuesToSingleType(parameters);
        let coercedType = coercedParams[0].valueType;
        if (coercedType == Value_1.ValueType.Int) {
            return this.CallType(coercedParams);
        }
        else if (coercedType == Value_1.ValueType.Float) {
            return this.CallType(coercedParams);
        }
        else if (coercedType == Value_1.ValueType.String) {
            return this.CallType(coercedParams);
        }
        else if (coercedType == Value_1.ValueType.DivertTarget) {
            return this.CallType(coercedParams);
        }
        else if (coercedType == Value_1.ValueType.List) {
            return this.CallType(coercedParams);
        }
        return null;
    }
    CallType(parametersOfSingleType) {
        let param1 = (0, TypeAssertion_1.asOrThrows)(parametersOfSingleType[0], Value_1.Value);
        let valType = param1.valueType;
        let val1 = param1;
        let paramCount = parametersOfSingleType.length;
        if (paramCount == 2 || paramCount == 1) {
            if (this._operationFuncs === null)
                return (0, NullException_1.throwNullException)("NativeFunctionCall._operationFuncs");
            let opForTypeObj = this._operationFuncs.get(valType);
            if (!opForTypeObj) {
                const key = Value_1.ValueType[valType];
                throw new StoryException_1.StoryException("Cannot perform operation " + this.name + " on " + key);
            }
            if (paramCount == 2) {
                let param2 = (0, TypeAssertion_1.asOrThrows)(parametersOfSingleType[1], Value_1.Value);
                let val2 = param2;
                let opForType = opForTypeObj;
                if (val1.value === null || val2.value === null)
                    return (0, NullException_1.throwNullException)("NativeFunctionCall.Call BinaryOp values");
                let resultVal = opForType(val1.value, val2.value);
                return Value_1.Value.Create(resultVal);
            }
            else {
                let opForType = opForTypeObj;
                if (val1.value === null)
                    return (0, NullException_1.throwNullException)("NativeFunctionCall.Call UnaryOp value");
                let resultVal = opForType(val1.value);
                // This code is different from upstream. Since JavaScript treats
                // integers and floats as the same numbers, it's impossible
                // to force an number to be either an integer or a float.
                //
                // It can be useful to force a specific number type
                // (especially for divisions), so the result of INT() & FLOAT()
                // is coerced to the the proper value type.
                //
                // Note that we also force all other unary operation to
                // return the same value type, although this is only
                // meaningful for numbers. See `Value.Create`.
                if (this.name === NativeFunctionCall.Int) {
                    return Value_1.Value.Create(resultVal, Value_1.ValueType.Int);
                }
                else if (this.name === NativeFunctionCall.Float) {
                    return Value_1.Value.Create(resultVal, Value_1.ValueType.Float);
                }
                else {
                    return Value_1.Value.Create(resultVal, param1.valueType);
                }
            }
        }
        else {
            throw new Error("Unexpected number of parameters to NativeFunctionCall: " +
                parametersOfSingleType.length);
        }
    }
    CallBinaryListOperation(parameters) {
        if ((this.name == "+" || this.name == "-") &&
            parameters[0] instanceof Value_1.ListValue &&
            parameters[1] instanceof Value_1.IntValue)
            return this.CallListIncrementOperation(parameters);
        let v1 = (0, TypeAssertion_1.asOrThrows)(parameters[0], Value_1.Value);
        let v2 = (0, TypeAssertion_1.asOrThrows)(parameters[1], Value_1.Value);
        if ((this.name == "&&" || this.name == "||") &&
            (v1.valueType != Value_1.ValueType.List || v2.valueType != Value_1.ValueType.List)) {
            if (this._operationFuncs === null)
                return (0, NullException_1.throwNullException)("NativeFunctionCall._operationFuncs");
            let op = this._operationFuncs.get(Value_1.ValueType.Int);
            if (op === null)
                return (0, NullException_1.throwNullException)("NativeFunctionCall.CallBinaryListOperation op");
            let result = (0, TypeAssertion_1.asBooleanOrThrows)(op(v1.isTruthy ? 1 : 0, v2.isTruthy ? 1 : 0));
            return new Value_1.BoolValue(result);
        }
        if (v1.valueType == Value_1.ValueType.List && v2.valueType == Value_1.ValueType.List)
            return this.CallType([v1, v2]);
        throw new StoryException_1.StoryException("Can not call use " +
            this.name +
            " operation on " +
            Value_1.ValueType[v1.valueType] +
            " and " +
            Value_1.ValueType[v2.valueType]);
    }
    CallListIncrementOperation(listIntParams) {
        let listVal = (0, TypeAssertion_1.asOrThrows)(listIntParams[0], Value_1.ListValue);
        let intVal = (0, TypeAssertion_1.asOrThrows)(listIntParams[1], Value_1.IntValue);
        let resultInkList = new InkList_1.InkList();
        if (listVal.value === null)
            return (0, NullException_1.throwNullException)("NativeFunctionCall.CallListIncrementOperation listVal.value");
        for (let [listItemKey, listItemValue] of listVal.value) {
            let listItem = InkList_1.InkListItem.fromSerializedKey(listItemKey);
            if (this._operationFuncs === null)
                return (0, NullException_1.throwNullException)("NativeFunctionCall._operationFuncs");
            let intOp = this._operationFuncs.get(Value_1.ValueType.Int);
            if (intVal.value === null)
                return (0, NullException_1.throwNullException)("NativeFunctionCall.CallListIncrementOperation intVal.value");
            let targetInt = intOp(listItemValue, intVal.value);
            let itemOrigin = null;
            if (listVal.value.origins === null)
                return (0, NullException_1.throwNullException)("NativeFunctionCall.CallListIncrementOperation listVal.value.origins");
            for (let origin of listVal.value.origins) {
                if (origin.name == listItem.originName) {
                    itemOrigin = origin;
                    break;
                }
            }
            if (itemOrigin != null) {
                let incrementedItem = itemOrigin.TryGetItemWithValue(targetInt, InkList_1.InkListItem.Null);
                if (incrementedItem.exists)
                    resultInkList.Add(incrementedItem.result, targetInt);
            }
        }
        return new Value_1.ListValue(resultInkList);
    }
    CoerceValuesToSingleType(parametersIn) {
        let valType = Value_1.ValueType.Int;
        let specialCaseList = null;
        for (let obj of parametersIn) {
            let val = (0, TypeAssertion_1.asOrThrows)(obj, Value_1.Value);
            if (val.valueType > valType) {
                valType = val.valueType;
            }
            if (val.valueType == Value_1.ValueType.List) {
                specialCaseList = (0, TypeAssertion_1.asOrNull)(val, Value_1.ListValue);
            }
        }
        let parametersOut = [];
        if (Value_1.ValueType[valType] == Value_1.ValueType[Value_1.ValueType.List]) {
            for (let inkObjectVal of parametersIn) {
                let val = (0, TypeAssertion_1.asOrThrows)(inkObjectVal, Value_1.Value);
                if (val.valueType == Value_1.ValueType.List) {
                    parametersOut.push(val);
                }
                else if (val.valueType == Value_1.ValueType.Int) {
                    let intVal = parseInt(val.valueObject);
                    specialCaseList = (0, TypeAssertion_1.asOrThrows)(specialCaseList, Value_1.ListValue);
                    if (specialCaseList.value === null)
                        return (0, NullException_1.throwNullException)("NativeFunctionCall.CoerceValuesToSingleType specialCaseList.value");
                    let list = specialCaseList.value.originOfMaxItem;
                    if (list === null)
                        return (0, NullException_1.throwNullException)("NativeFunctionCall.CoerceValuesToSingleType list");
                    let item = list.TryGetItemWithValue(intVal, InkList_1.InkListItem.Null);
                    if (item.exists) {
                        let castedValue = new Value_1.ListValue(item.result, intVal);
                        parametersOut.push(castedValue);
                    }
                    else
                        throw new StoryException_1.StoryException("Could not find List item with the value " +
                            intVal +
                            " in " +
                            list.name);
                }
                else {
                    const key = Value_1.ValueType[val.valueType];
                    throw new StoryException_1.StoryException("Cannot mix Lists and " + key + " values in this operation");
                }
            }
        }
        else {
            for (let inkObjectVal of parametersIn) {
                let val = (0, TypeAssertion_1.asOrThrows)(inkObjectVal, Value_1.Value);
                let castedValue = val.Cast(valType);
                parametersOut.push(castedValue);
            }
        }
        return parametersOut;
    }
    constructor() {
        super();
        this._name = null;
        this._numberOfParameters = 0;
        this._prototype = null;
        this._isPrototype = false;
        this._operationFuncs = null;
        if (arguments.length === 0) {
            NativeFunctionCall.GenerateNativeFunctionsIfNecessary();
        }
        else if (arguments.length === 1) {
            let name = arguments[0];
            NativeFunctionCall.GenerateNativeFunctionsIfNecessary();
            this.name = name;
        }
        else if (arguments.length === 2) {
            let name = arguments[0];
            let numberOfParameters = arguments[1];
            this._isPrototype = true;
            this.name = name;
            this.numberOfParameters = numberOfParameters;
        }
    }
    static Identity(t) {
        return t;
    }
    static GenerateNativeFunctionsIfNecessary() {
        if (this._nativeFunctions == null) {
            this._nativeFunctions = new Map();
            // Int operations
            this.AddIntBinaryOp(this.Add, (x, y) => x + y);
            this.AddIntBinaryOp(this.Subtract, (x, y) => x - y);
            this.AddIntBinaryOp(this.Multiply, (x, y) => x * y);
            this.AddIntBinaryOp(this.Divide, (x, y) => Math.floor(x / y));
            this.AddIntBinaryOp(this.Mod, (x, y) => x % y);
            this.AddIntUnaryOp(this.Negate, (x) => -x);
            this.AddIntBinaryOp(this.Equal, (x, y) => x == y);
            this.AddIntBinaryOp(this.Greater, (x, y) => x > y);
            this.AddIntBinaryOp(this.Less, (x, y) => x < y);
            this.AddIntBinaryOp(this.GreaterThanOrEquals, (x, y) => x >= y);
            this.AddIntBinaryOp(this.LessThanOrEquals, (x, y) => x <= y);
            this.AddIntBinaryOp(this.NotEquals, (x, y) => x != y);
            this.AddIntUnaryOp(this.Not, (x) => x == 0);
            this.AddIntBinaryOp(this.And, (x, y) => x != 0 && y != 0);
            this.AddIntBinaryOp(this.Or, (x, y) => x != 0 || y != 0);
            this.AddIntBinaryOp(this.Max, (x, y) => Math.max(x, y));
            this.AddIntBinaryOp(this.Min, (x, y) => Math.min(x, y));
            this.AddIntBinaryOp(this.Pow, (x, y) => Math.pow(x, y));
            this.AddIntUnaryOp(this.Floor, NativeFunctionCall.Identity);
            this.AddIntUnaryOp(this.Ceiling, NativeFunctionCall.Identity);
            this.AddIntUnaryOp(this.Int, NativeFunctionCall.Identity);
            this.AddIntUnaryOp(this.Float, (x) => x);
            // Float operations
            this.AddFloatBinaryOp(this.Add, (x, y) => x + y);
            this.AddFloatBinaryOp(this.Subtract, (x, y) => x - y);
            this.AddFloatBinaryOp(this.Multiply, (x, y) => x * y);
            this.AddFloatBinaryOp(this.Divide, (x, y) => x / y);
            this.AddFloatBinaryOp(this.Mod, (x, y) => x % y);
            this.AddFloatUnaryOp(this.Negate, (x) => -x);
            this.AddFloatBinaryOp(this.Equal, (x, y) => x == y);
            this.AddFloatBinaryOp(this.Greater, (x, y) => x > y);
            this.AddFloatBinaryOp(this.Less, (x, y) => x < y);
            this.AddFloatBinaryOp(this.GreaterThanOrEquals, (x, y) => x >= y);
            this.AddFloatBinaryOp(this.LessThanOrEquals, (x, y) => x <= y);
            this.AddFloatBinaryOp(this.NotEquals, (x, y) => x != y);
            this.AddFloatUnaryOp(this.Not, (x) => x == 0.0);
            this.AddFloatBinaryOp(this.And, (x, y) => x != 0.0 && y != 0.0);
            this.AddFloatBinaryOp(this.Or, (x, y) => x != 0.0 || y != 0.0);
            this.AddFloatBinaryOp(this.Max, (x, y) => Math.max(x, y));
            this.AddFloatBinaryOp(this.Min, (x, y) => Math.min(x, y));
            this.AddFloatBinaryOp(this.Pow, (x, y) => Math.pow(x, y));
            this.AddFloatUnaryOp(this.Floor, (x) => Math.floor(x));
            this.AddFloatUnaryOp(this.Ceiling, (x) => Math.ceil(x));
            this.AddFloatUnaryOp(this.Int, (x) => Math.floor(x));
            this.AddFloatUnaryOp(this.Float, NativeFunctionCall.Identity);
            // String operations
            this.AddStringBinaryOp(this.Add, (x, y) => x + y); // concat
            this.AddStringBinaryOp(this.Equal, (x, y) => x === y);
            this.AddStringBinaryOp(this.NotEquals, (x, y) => !(x === y));
            this.AddStringBinaryOp(this.Has, (x, y) => x.includes(y));
            this.AddStringBinaryOp(this.Hasnt, (x, y) => !x.includes(y));
            this.AddListBinaryOp(this.Add, (x, y) => x.Union(y));
            this.AddListBinaryOp(this.Subtract, (x, y) => x.Without(y));
            this.AddListBinaryOp(this.Has, (x, y) => x.Contains(y));
            this.AddListBinaryOp(this.Hasnt, (x, y) => !x.Contains(y));
            this.AddListBinaryOp(this.Intersect, (x, y) => x.Intersect(y));
            this.AddListBinaryOp(this.Equal, (x, y) => x.Equals(y));
            this.AddListBinaryOp(this.Greater, (x, y) => x.GreaterThan(y));
            this.AddListBinaryOp(this.Less, (x, y) => x.LessThan(y));
            this.AddListBinaryOp(this.GreaterThanOrEquals, (x, y) => x.GreaterThanOrEquals(y));
            this.AddListBinaryOp(this.LessThanOrEquals, (x, y) => x.LessThanOrEquals(y));
            this.AddListBinaryOp(this.NotEquals, (x, y) => !x.Equals(y));
            this.AddListBinaryOp(this.And, (x, y) => x.Count > 0 && y.Count > 0);
            this.AddListBinaryOp(this.Or, (x, y) => x.Count > 0 || y.Count > 0);
            this.AddListUnaryOp(this.Not, (x) => (x.Count == 0 ? 1 : 0));
            this.AddListUnaryOp(this.Invert, (x) => x.inverse);
            this.AddListUnaryOp(this.All, (x) => x.all);
            this.AddListUnaryOp(this.ListMin, (x) => x.MinAsList());
            this.AddListUnaryOp(this.ListMax, (x) => x.MaxAsList());
            this.AddListUnaryOp(this.Count, (x) => x.Count);
            this.AddListUnaryOp(this.ValueOfList, (x) => x.maxItem.Value);
            let divertTargetsEqual = (d1, d2) => d1.Equals(d2);
            let divertTargetsNotEqual = (d1, d2) => !d1.Equals(d2);
            this.AddOpToNativeFunc(this.Equal, 2, Value_1.ValueType.DivertTarget, divertTargetsEqual);
            this.AddOpToNativeFunc(this.NotEquals, 2, Value_1.ValueType.DivertTarget, divertTargetsNotEqual);
        }
    }
    AddOpFuncForType(valType, op) {
        if (this._operationFuncs == null) {
            this._operationFuncs = new Map();
        }
        this._operationFuncs.set(valType, op);
    }
    static AddOpToNativeFunc(name, args, valType, op) {
        if (this._nativeFunctions === null)
            return (0, NullException_1.throwNullException)("NativeFunctionCall._nativeFunctions");
        let nativeFunc = this._nativeFunctions.get(name);
        if (!nativeFunc) {
            nativeFunc = new NativeFunctionCall(name, args);
            this._nativeFunctions.set(name, nativeFunc);
        }
        nativeFunc.AddOpFuncForType(valType, op);
    }
    static AddIntBinaryOp(name, op) {
        this.AddOpToNativeFunc(name, 2, Value_1.ValueType.Int, op);
    }
    static AddIntUnaryOp(name, op) {
        this.AddOpToNativeFunc(name, 1, Value_1.ValueType.Int, op);
    }
    static AddFloatBinaryOp(name, op) {
        this.AddOpToNativeFunc(name, 2, Value_1.ValueType.Float, op);
    }
    static AddFloatUnaryOp(name, op) {
        this.AddOpToNativeFunc(name, 1, Value_1.ValueType.Float, op);
    }
    static AddStringBinaryOp(name, op) {
        this.AddOpToNativeFunc(name, 2, Value_1.ValueType.String, op);
    }
    static AddListBinaryOp(name, op) {
        this.AddOpToNativeFunc(name, 2, Value_1.ValueType.List, op);
    }
    static AddListUnaryOp(name, op) {
        this.AddOpToNativeFunc(name, 1, Value_1.ValueType.List, op);
    }
    toString() {
        return 'Native "' + this.name + '"';
    }
}
exports.NativeFunctionCall = NativeFunctionCall;
NativeFunctionCall.Add = "+";
NativeFunctionCall.Subtract = "-";
NativeFunctionCall.Divide = "/";
NativeFunctionCall.Multiply = "*";
NativeFunctionCall.Mod = "%";
NativeFunctionCall.Negate = "_";
NativeFunctionCall.Equal = "==";
NativeFunctionCall.Greater = ">";
NativeFunctionCall.Less = "<";
NativeFunctionCall.GreaterThanOrEquals = ">=";
NativeFunctionCall.LessThanOrEquals = "<=";
NativeFunctionCall.NotEquals = "!=";
NativeFunctionCall.Not = "!";
NativeFunctionCall.And = "&&";
NativeFunctionCall.Or = "||";
NativeFunctionCall.Min = "MIN";
NativeFunctionCall.Max = "MAX";
NativeFunctionCall.Pow = "POW";
NativeFunctionCall.Floor = "FLOOR";
NativeFunctionCall.Ceiling = "CEILING";
NativeFunctionCall.Int = "INT";
NativeFunctionCall.Float = "FLOAT";
NativeFunctionCall.Has = "?";
NativeFunctionCall.Hasnt = "!?";
NativeFunctionCall.Intersect = "^";
NativeFunctionCall.ListMin = "LIST_MIN";
NativeFunctionCall.ListMax = "LIST_MAX";
NativeFunctionCall.All = "LIST_ALL";
NativeFunctionCall.Count = "LIST_COUNT";
NativeFunctionCall.ValueOfList = "LIST_VALUE";
NativeFunctionCall.Invert = "LIST_INVERT";
NativeFunctionCall._nativeFunctions = null;
//# sourceMappingURL=NativeFunctionCall.js.map