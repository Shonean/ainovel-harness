"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.SimpleJson = void 0;
class SimpleJson {
    static TextToDictionary(text) {
        return new SimpleJson.Reader(text).ToDictionary();
    }
    static TextToArray(text) {
        return new SimpleJson.Reader(text).ToArray();
    }
}
exports.SimpleJson = SimpleJson;
(function (SimpleJson) {
    class Reader {
        constructor(text) {
            // Before parsing the JSON, all floats of the form "123.0" are transformed into "123.0f"
            // so that they are recognized as FLOAT in the ink runtime
            const nativeFloatParsing = JSON.parse("0", 
            // @ts-expect-error : typing
            (_, __, context) => context != null);
            if (!nativeFloatParsing) {
                // When the nativeFloatParsing argument is false,
                // we aggressively replace using a regexp
                // At time of writing : only happen for Safari iOS and Mac
                const jsonWithExplicitFloat = text.replace(/(,\s*)([0-9]+\.[0]+)([,]*)/g, '$1"$2f"$3');
                this._rootObject = JSON.parse(jsonWithExplicitFloat);
            }
            else {
                // @ts-expect-error : typing
                const explicitFloatReviver = (_, value, context) => {
                    // When the nativeFloatParsing argument is true,
                    // we use a custom reviver function
                    //see https://github.com/y-lohse/inkjs/pull/1100#issuecomment-2733148441
                    if (Number.isInteger(value) && context.source.endsWith(".0")) {
                        return context.source + "f";
                    }
                    return value;
                };
                // @ts-expect-error : typing
                this._rootObject = JSON.parse(text, explicitFloatReviver);
            }
        }
        ToDictionary() {
            return this._rootObject;
        }
        ToArray() {
            return this._rootObject;
        }
    }
    SimpleJson.Reader = Reader;
    // In C#, this class writes json tokens directly to a StringWriter or
    // another stream. Here, a temporary hierarchy is created in the form
    // of a javascript object, which is serialised in the `toString` method.
    // See individual methods and properties for more information.
    class Writer {
        constructor() {
            // In addition to `_stateStack` present in the original code,
            // this implementation of SimpleJson use two other stacks and two
            // temporary variables holding the current context.
            // Used to keep track of the current property name being built
            // with `WritePropertyNameStart`, `WritePropertyNameInner` and
            // `WritePropertyNameEnd`.
            this._currentPropertyName = null;
            // Used to keep track of the current string value being built
            // with `WriteStringStart`, `WriteStringInner` and
            // `WriteStringEnd`.
            this._currentString = null;
            this._stateStack = [];
            // Keep track of the current collection being built (either an array
            // or an object). For instance, at the '?' step during the hiarchy
            // creation, this hierarchy:
            // [3, {a: [b, ?]}] will have this corresponding stack:
            // (bottom) [Array, Object, Array] (top)
            this._collectionStack = [];
            // Keep track of the current property being assigned. For instance, at
            // the '?' step during the hiarchy creation, this hierarchy:
            // [3, {a: [b, {c: ?}]}] will have this corresponding stack:
            // (bottom) [a, c] (top)
            this._propertyNameStack = [];
            // Object containing the entire hiearchy.
            this._jsonObject = null;
        }
        WriteObject(inner) {
            this.WriteObjectStart();
            inner(this);
            this.WriteObjectEnd();
        }
        // Add a new object.
        WriteObjectStart() {
            this.StartNewObject(true);
            let newObject = {};
            if (this.state === SimpleJson.Writer.State.Property) {
                // This object is created as the value of a property,
                // inside an other object.
                this.Assert(this.currentCollection !== null);
                this.Assert(this.currentPropertyName !== null);
                let propertyName = this._propertyNameStack.pop();
                this.currentCollection[propertyName] = newObject;
                this._collectionStack.push(newObject);
            }
            else if (this.state === SimpleJson.Writer.State.Array) {
                // This object is created as the child of an array.
                this.Assert(this.currentCollection !== null);
                this.currentCollection.push(newObject);
                this._collectionStack.push(newObject);
            }
            else {
                // This object is the root object.
                this.Assert(this.state === SimpleJson.Writer.State.None);
                this._jsonObject = newObject;
                this._collectionStack.push(newObject);
            }
            this._stateStack.push(new SimpleJson.Writer.StateElement(SimpleJson.Writer.State.Object));
        }
        WriteObjectEnd() {
            this.Assert(this.state === SimpleJson.Writer.State.Object);
            this._collectionStack.pop();
            this._stateStack.pop();
        }
        // Write a property name / value pair to the current object.
        WriteProperty(name, 
        // eslint-disable-next-line @typescript-eslint/no-unused-vars
        innerOrContent) {
            this.WritePropertyStart(name);
            if (arguments[1] instanceof Function) {
                let inner = arguments[1];
                inner(this);
            }
            else {
                let content = arguments[1];
                this.Write(content);
            }
            this.WritePropertyEnd();
        }
        // Int and Float are separate calls, since there both are
        // numbers in JavaScript, but need to be handled differently.
        WriteIntProperty(name, content) {
            this.WritePropertyStart(name);
            this.WriteInt(content);
            this.WritePropertyEnd();
        }
        WriteFloatProperty(name, content) {
            this.WritePropertyStart(name);
            this.WriteFloat(content);
            this.WritePropertyEnd();
        }
        // Prepare a new property name, which will be use to add the
        // new object when calling _addToCurrentObject() from a Write
        // method.
        WritePropertyStart(name) {
            this.Assert(this.state === SimpleJson.Writer.State.Object);
            this._propertyNameStack.push(name);
            this.IncrementChildCount();
            this._stateStack.push(new SimpleJson.Writer.StateElement(SimpleJson.Writer.State.Property));
        }
        WritePropertyEnd() {
            this.Assert(this.state === SimpleJson.Writer.State.Property);
            this.Assert(this.childCount === 1);
            this._stateStack.pop();
        }
        // Prepare a new property name, except this time, the property name
        // will be created by concatenating all the strings passed to
        // WritePropertyNameInner.
        WritePropertyNameStart() {
            this.Assert(this.state === SimpleJson.Writer.State.Object);
            this.IncrementChildCount();
            this._currentPropertyName = "";
            this._stateStack.push(new SimpleJson.Writer.StateElement(SimpleJson.Writer.State.Property));
            this._stateStack.push(new SimpleJson.Writer.StateElement(SimpleJson.Writer.State.PropertyName));
        }
        WritePropertyNameEnd() {
            this.Assert(this.state === SimpleJson.Writer.State.PropertyName);
            this.Assert(this._currentPropertyName !== null);
            this._propertyNameStack.push(this._currentPropertyName);
            this._currentPropertyName = null;
            this._stateStack.pop();
        }
        WritePropertyNameInner(str) {
            this.Assert(this.state === SimpleJson.Writer.State.PropertyName);
            this.Assert(this._currentPropertyName !== null);
            this._currentPropertyName += str;
        }
        // Add a new array.
        WriteArrayStart() {
            this.StartNewObject(true);
            let newObject = [];
            if (this.state === SimpleJson.Writer.State.Property) {
                // This array is created as the value of a property,
                // inside an object.
                this.Assert(this.currentCollection !== null);
                this.Assert(this.currentPropertyName !== null);
                let propertyName = this._propertyNameStack.pop();
                this.currentCollection[propertyName] = newObject;
                this._collectionStack.push(newObject);
            }
            else if (this.state === SimpleJson.Writer.State.Array) {
                // This array is created as the child of another array.
                this.Assert(this.currentCollection !== null);
                this.currentCollection.push(newObject);
                this._collectionStack.push(newObject);
            }
            else {
                // This array is the root object.
                this.Assert(this.state === SimpleJson.Writer.State.None);
                this._jsonObject = newObject;
                this._collectionStack.push(newObject);
            }
            this._stateStack.push(new SimpleJson.Writer.StateElement(SimpleJson.Writer.State.Array));
        }
        WriteArrayEnd() {
            this.Assert(this.state === SimpleJson.Writer.State.Array);
            this._collectionStack.pop();
            this._stateStack.pop();
        }
        // Add the value to the appropriate collection (array / object), given the current
        // context.
        Write(value, 
        // eslint-disable-next-line @typescript-eslint/no-unused-vars
        escape = true) {
            if (value === null) {
                console.error("Warning: trying to write a null value");
                return;
            }
            this.StartNewObject(false);
            this._addToCurrentObject(value);
        }
        WriteBool(value) {
            if (value === null) {
                return;
            }
            this.StartNewObject(false);
            this._addToCurrentObject(value);
        }
        WriteInt(value) {
            if (value === null) {
                return;
            }
            this.StartNewObject(false);
            // Math.floor is used as a precaution:
            //     1. to ensure that the value is written as an integer
            //        (without a fractional part -> 1 instead of 1.0), even
            //        though it should be the default behaviour of
            //        JSON.serialize;
            //     2. to ensure that if a floating number is passed
            //        accidentally, it's converted to an integer.
            //
            // This guarantees savegame compatibility with the reference
            // implementation.
            this._addToCurrentObject(Math.floor(value));
        }
        // Since JSON doesn't support NaN and Infinity, these values
        // are converted here.
        WriteFloat(value) {
            if (value === null) {
                return;
            }
            this.StartNewObject(false);
            if (value == Number.POSITIVE_INFINITY) {
                this._addToCurrentObject(3.4e38);
            }
            else if (value == Number.NEGATIVE_INFINITY) {
                this._addToCurrentObject(-3.4e38);
            }
            else if (isNaN(value)) {
                this._addToCurrentObject(0.0);
            }
            else {
                this._addToCurrentObject(value);
            }
        }
        WriteNull() {
            this.StartNewObject(false);
            this._addToCurrentObject(null);
        }
        // Prepare a string before adding it to the current collection in
        // WriteStringEnd(). The string will be a concatenation of all the
        // strings passed to WriteStringInner.
        WriteStringStart() {
            this.StartNewObject(false);
            this._currentString = "";
            this._stateStack.push(new SimpleJson.Writer.StateElement(SimpleJson.Writer.State.String));
        }
        WriteStringEnd() {
            this.Assert(this.state == SimpleJson.Writer.State.String);
            this._stateStack.pop();
            this._addToCurrentObject(this._currentString);
            this._currentString = null;
        }
        // eslint-disable-next-line @typescript-eslint/no-unused-vars
        WriteStringInner(str, escape = true) {
            this.Assert(this.state === SimpleJson.Writer.State.String);
            if (str === null) {
                console.error("Warning: trying to write a null string");
                return;
            }
            this._currentString += str;
        }
        // Serialise the root object into a JSON string.
        toString() {
            if (this._jsonObject === null) {
                return "";
            }
            return JSON.stringify(this._jsonObject);
        }
        // Prepare the state stack when adding new objects / values.
        StartNewObject(container) {
            if (container) {
                this.Assert(this.state === SimpleJson.Writer.State.None ||
                    this.state === SimpleJson.Writer.State.Property ||
                    this.state === SimpleJson.Writer.State.Array);
            }
            else {
                this.Assert(this.state === SimpleJson.Writer.State.Property ||
                    this.state === SimpleJson.Writer.State.Array);
            }
            if (this.state === SimpleJson.Writer.State.Property) {
                this.Assert(this.childCount === 0);
            }
            if (this.state === SimpleJson.Writer.State.Array ||
                this.state === SimpleJson.Writer.State.Property) {
                this.IncrementChildCount();
            }
        }
        // These getters peek all the different stacks.
        get state() {
            if (this._stateStack.length > 0) {
                return this._stateStack[this._stateStack.length - 1].type;
            }
            else {
                return SimpleJson.Writer.State.None;
            }
        }
        get childCount() {
            if (this._stateStack.length > 0) {
                return this._stateStack[this._stateStack.length - 1].childCount;
            }
            else {
                return 0;
            }
        }
        get currentCollection() {
            if (this._collectionStack.length > 0) {
                return this._collectionStack[this._collectionStack.length - 1];
            }
            else {
                return null;
            }
        }
        get currentPropertyName() {
            if (this._propertyNameStack.length > 0) {
                return this._propertyNameStack[this._propertyNameStack.length - 1];
            }
            else {
                return null;
            }
        }
        IncrementChildCount() {
            this.Assert(this._stateStack.length > 0);
            let currEl = this._stateStack.pop();
            currEl.childCount++;
            this._stateStack.push(currEl);
        }
        Assert(condition) {
            if (!condition)
                throw Error("Assert failed while writing JSON");
        }
        // This method did not exist in the original C# code. It adds
        // the given value to the current collection (used by Write methods).
        _addToCurrentObject(value) {
            this.Assert(this.currentCollection !== null);
            if (this.state === SimpleJson.Writer.State.Array) {
                this.Assert(Array.isArray(this.currentCollection));
                this.currentCollection.push(value);
            }
            else if (this.state === SimpleJson.Writer.State.Property) {
                this.Assert(!Array.isArray(this.currentCollection));
                this.Assert(this.currentPropertyName !== null);
                this.currentCollection[this.currentPropertyName] = value;
                this._propertyNameStack.pop();
            }
        }
    }
    SimpleJson.Writer = Writer;
    (function (Writer) {
        let State;
        (function (State) {
            State[State["None"] = 0] = "None";
            State[State["Object"] = 1] = "Object";
            State[State["Array"] = 2] = "Array";
            State[State["Property"] = 3] = "Property";
            State[State["PropertyName"] = 4] = "PropertyName";
            State[State["String"] = 5] = "String";
        })(State = Writer.State || (Writer.State = {}));
        class StateElement {
            constructor(type) {
                this.type = SimpleJson.Writer.State.None;
                this.childCount = 0;
                this.type = type;
            }
        }
        Writer.StateElement = StateElement;
    })(Writer = SimpleJson.Writer || (SimpleJson.Writer = {}));
})(SimpleJson || (exports.SimpleJson = SimpleJson = {}));
//# sourceMappingURL=SimpleJson.js.map