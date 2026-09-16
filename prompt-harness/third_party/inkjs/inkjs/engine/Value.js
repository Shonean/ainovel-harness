"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.ValueType = exports.ListValue = exports.VariablePointerValue = exports.DivertTargetValue = exports.StringValue = exports.FloatValue = exports.IntValue = exports.BoolValue = exports.Value = exports.AbstractValue = void 0;
const Object_1 = require("./Object");
const Path_1 = require("./Path");
const InkList_1 = require("./InkList");
const StoryException_1 = require("./StoryException");
const TypeAssertion_1 = require("./TypeAssertion");
const TryGetResult_1 = require("./TryGetResult");
const NullException_1 = require("./NullException");
class AbstractValue extends Object_1.InkObject {
    static Create(val, preferredNumberType) {
        // This code doesn't exist in upstream and is simply here to enforce
        // the creation of the proper number value.
        // If `preferredNumberType` is not provided or if value doesn't match
        // `preferredNumberType`, this conditional does nothing.
        if (preferredNumberType) {
            if (preferredNumberType === ValueType.Int &&
                Number.isInteger(Number(val))) {
                return new IntValue(Number(val));
            }
            else if (preferredNumberType === ValueType.Float &&
                !isNaN(val)) {
                return new FloatValue(Number(val));
            }
        }
        if (typeof val === "boolean") {
            return new BoolValue(Boolean(val));
        }
        // https://github.com/y-lohse/inkjs/issues/425
        // Changed condition sequence, because Number('') is
        // parsed to 0, which made setting string to empty
        // impossible
        if (typeof val === "string") {
            return new StringValue(String(val));
        }
        else if (Number.isInteger(Number(val))) {
            return new IntValue(Number(val));
        }
        else if (!isNaN(val)) {
            return new FloatValue(Number(val));
        }
        else if (val instanceof Path_1.Path) {
            return new DivertTargetValue((0, TypeAssertion_1.asOrThrows)(val, Path_1.Path));
        }
        else if (val instanceof InkList_1.InkList) {
            return new ListValue((0, TypeAssertion_1.asOrThrows)(val, InkList_1.InkList));
        }
        return null;
    }
    Copy() {
        return (0, TypeAssertion_1.asOrThrows)(AbstractValue.Create(this.valueObject), Object_1.InkObject);
    }
    BadCastException(targetType) {
        return new StoryException_1.StoryException("Can't cast " +
            this.valueObject +
            " from " +
            this.valueType +
            " to " +
            targetType);
    }
}
exports.AbstractValue = AbstractValue;
class Value extends AbstractValue {
    constructor(val) {
        super();
        this.value = val;
    }
    get valueObject() {
        return this.value;
    }
    toString() {
        if (this.value === null)
            return (0, NullException_1.throwNullException)("Value.value");
        return this.value.toString();
    }
}
exports.Value = Value;
class BoolValue extends Value {
    constructor(val) {
        super(val || false);
    }
    get isTruthy() {
        return Boolean(this.value);
    }
    get valueType() {
        return ValueType.Bool;
    }
    Cast(newType) {
        if (this.value === null)
            return (0, NullException_1.throwNullException)("Value.value");
        if (newType == this.valueType) {
            return this;
        }
        if (newType == ValueType.Int) {
            return new IntValue(this.value ? 1 : 0);
        }
        if (newType == ValueType.Float) {
            return new FloatValue(this.value ? 1.0 : 0.0);
        }
        if (newType == ValueType.String) {
            return new StringValue(this.value ? "true" : "false");
        }
        throw this.BadCastException(newType);
    }
    toString() {
        return this.value ? "true" : "false";
    }
}
exports.BoolValue = BoolValue;
class IntValue extends Value {
    constructor(val) {
        super(val || 0);
    }
    get isTruthy() {
        return this.value != 0;
    }
    get valueType() {
        return ValueType.Int;
    }
    Cast(newType) {
        if (this.value === null)
            return (0, NullException_1.throwNullException)("Value.value");
        if (newType == this.valueType) {
            return this;
        }
        if (newType == ValueType.Bool) {
            return new BoolValue(this.value === 0 ? false : true);
        }
        if (newType == ValueType.Float) {
            return new FloatValue(this.value);
        }
        if (newType == ValueType.String) {
            return new StringValue("" + this.value);
        }
        throw this.BadCastException(newType);
    }
}
exports.IntValue = IntValue;
class FloatValue extends Value {
    constructor(val) {
        super(val || 0.0);
    }
    get isTruthy() {
        return this.value != 0.0;
    }
    get valueType() {
        return ValueType.Float;
    }
    Cast(newType) {
        if (this.value === null)
            return (0, NullException_1.throwNullException)("Value.value");
        if (newType == this.valueType) {
            return this;
        }
        if (newType == ValueType.Bool) {
            return new BoolValue(this.value === 0.0 ? false : true);
        }
        if (newType == ValueType.Int) {
            return new IntValue(this.value);
        }
        if (newType == ValueType.String) {
            return new StringValue("" + this.value);
        }
        throw this.BadCastException(newType);
    }
}
exports.FloatValue = FloatValue;
class StringValue extends Value {
    constructor(val) {
        super(val || "");
        this._isNewline = this.value == "\n";
        this._isInlineWhitespace = true;
        if (this.value === null)
            return (0, NullException_1.throwNullException)("Value.value");
        if (this.value.length > 0) {
            this.value.split("").every((c) => {
                if (c != " " && c != "\t") {
                    this._isInlineWhitespace = false;
                    return false;
                }
                return true;
            });
        }
    }
    get valueType() {
        return ValueType.String;
    }
    get isTruthy() {
        if (this.value === null)
            return (0, NullException_1.throwNullException)("Value.value");
        return this.value.length > 0;
    }
    get isNewline() {
        return this._isNewline;
    }
    get isInlineWhitespace() {
        return this._isInlineWhitespace;
    }
    get isNonWhitespace() {
        return !this.isNewline && !this.isInlineWhitespace;
    }
    Cast(newType) {
        if (newType == this.valueType) {
            return this;
        }
        if (newType == ValueType.Int) {
            let parsedInt = (0, TryGetResult_1.tryParseInt)(this.value);
            if (parsedInt.exists) {
                return new IntValue(parsedInt.result);
            }
            else {
                throw this.BadCastException(newType);
            }
        }
        if (newType == ValueType.Float) {
            let parsedFloat = (0, TryGetResult_1.tryParseFloat)(this.value);
            if (parsedFloat.exists) {
                return new FloatValue(parsedFloat.result);
            }
            else {
                throw this.BadCastException(newType);
            }
        }
        throw this.BadCastException(newType);
    }
}
exports.StringValue = StringValue;
class DivertTargetValue extends Value {
    constructor(targetPath = null) {
        super(targetPath);
    }
    get valueType() {
        return ValueType.DivertTarget;
    }
    get targetPath() {
        if (this.value === null)
            return (0, NullException_1.throwNullException)("Value.value");
        return this.value;
    }
    set targetPath(value) {
        this.value = value;
    }
    get isTruthy() {
        throw new Error("Shouldn't be checking the truthiness of a divert target");
    }
    Cast(newType) {
        if (newType == this.valueType)
            return this;
        throw this.BadCastException(newType);
    }
    toString() {
        return "DivertTargetValue(" + this.targetPath + ")";
    }
}
exports.DivertTargetValue = DivertTargetValue;
class VariablePointerValue extends Value {
    constructor(variableName, contextIndex = -1) {
        super(variableName);
        this._contextIndex = contextIndex;
    }
    get contextIndex() {
        return this._contextIndex;
    }
    set contextIndex(value) {
        this._contextIndex = value;
    }
    get variableName() {
        if (this.value === null)
            return (0, NullException_1.throwNullException)("Value.value");
        return this.value;
    }
    set variableName(value) {
        this.value = value;
    }
    get valueType() {
        return ValueType.VariablePointer;
    }
    get isTruthy() {
        throw new Error("Shouldn't be checking the truthiness of a variable pointer");
    }
    Cast(newType) {
        if (newType == this.valueType)
            return this;
        throw this.BadCastException(newType);
    }
    toString() {
        return "VariablePointerValue(" + this.variableName + ")";
    }
    Copy() {
        return new VariablePointerValue(this.variableName, this.contextIndex);
    }
}
exports.VariablePointerValue = VariablePointerValue;
class ListValue extends Value {
    get isTruthy() {
        if (this.value === null) {
            return (0, NullException_1.throwNullException)("this.value");
        }
        return this.value.Count > 0;
    }
    get valueType() {
        return ValueType.List;
    }
    Cast(newType) {
        if (this.value === null)
            return (0, NullException_1.throwNullException)("Value.value");
        if (newType == ValueType.Int) {
            let max = this.value.maxItem;
            if (max.Key.isNull)
                return new IntValue(0);
            else
                return new IntValue(max.Value);
        }
        else if (newType == ValueType.Float) {
            let max = this.value.maxItem;
            if (max.Key.isNull)
                return new FloatValue(0.0);
            else
                return new FloatValue(max.Value);
        }
        else if (newType == ValueType.String) {
            let max = this.value.maxItem;
            if (max.Key.isNull)
                return new StringValue("");
            else {
                return new StringValue(max.Key.toString());
            }
        }
        if (newType == this.valueType)
            return this;
        throw this.BadCastException(newType);
    }
    constructor(listOrSingleItem, singleValue) {
        super(null);
        if (!listOrSingleItem && !singleValue) {
            this.value = new InkList_1.InkList();
        }
        else if (listOrSingleItem instanceof InkList_1.InkList) {
            this.value = new InkList_1.InkList(listOrSingleItem);
        }
        else if (listOrSingleItem instanceof InkList_1.InkListItem &&
            typeof singleValue === "number") {
            this.value = new InkList_1.InkList({
                Key: listOrSingleItem,
                Value: singleValue,
            });
        }
    }
    static RetainListOriginsForAssignment(oldValue, newValue) {
        let oldList = (0, TypeAssertion_1.asOrNull)(oldValue, ListValue);
        let newList = (0, TypeAssertion_1.asOrNull)(newValue, ListValue);
        if (newList && newList.value === null)
            return (0, NullException_1.throwNullException)("newList.value");
        if (oldList && oldList.value === null)
            return (0, NullException_1.throwNullException)("oldList.value");
        // When assigning the empty list, try to retain any initial origin names
        if (oldList && newList && newList.value.Count == 0)
            newList.value.SetInitialOriginNames(oldList.value.originNames);
    }
}
exports.ListValue = ListValue;
var ValueType;
(function (ValueType) {
    ValueType[ValueType["Bool"] = -1] = "Bool";
    ValueType[ValueType["Int"] = 0] = "Int";
    ValueType[ValueType["Float"] = 1] = "Float";
    ValueType[ValueType["List"] = 2] = "List";
    ValueType[ValueType["String"] = 3] = "String";
    ValueType[ValueType["DivertTarget"] = 4] = "DivertTarget";
    ValueType[ValueType["VariablePointer"] = 5] = "VariablePointer";
})(ValueType || (exports.ValueType = ValueType = {}));
//# sourceMappingURL=Value.js.map