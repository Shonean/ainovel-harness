"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.JsonSerialisation = void 0;
const Container_1 = require("./Container");
const Value_1 = require("./Value");
const Glue_1 = require("./Glue");
const ControlCommand_1 = require("./ControlCommand");
const PushPop_1 = require("./PushPop");
const Divert_1 = require("./Divert");
const ChoicePoint_1 = require("./ChoicePoint");
const VariableReference_1 = require("./VariableReference");
const VariableAssignment_1 = require("./VariableAssignment");
const NativeFunctionCall_1 = require("./NativeFunctionCall");
const Void_1 = require("./Void");
const Tag_1 = require("./Tag");
const Path_1 = require("./Path");
const Choice_1 = require("./Choice");
const ListDefinition_1 = require("./ListDefinition");
const ListDefinitionsOrigin_1 = require("./ListDefinitionsOrigin");
const InkList_1 = require("./InkList");
const TypeAssertion_1 = require("./TypeAssertion");
const NullException_1 = require("./NullException");
class JsonSerialisation {
    static JArrayToRuntimeObjList(jArray, skipLast = false) {
        let count = jArray.length;
        if (skipLast)
            count--;
        let list = [];
        for (let i = 0; i < count; i++) {
            let jTok = jArray[i];
            let runtimeObj = this.JTokenToRuntimeObject(jTok);
            if (runtimeObj === null) {
                return (0, NullException_1.throwNullException)("runtimeObj");
            }
            list.push(runtimeObj);
        }
        return list;
    }
    static WriteDictionaryRuntimeObjs(writer, dictionary) {
        writer.WriteObjectStart();
        for (let [key, value] of dictionary) {
            writer.WritePropertyStart(key);
            this.WriteRuntimeObject(writer, value);
            writer.WritePropertyEnd();
        }
        writer.WriteObjectEnd();
    }
    static WriteListRuntimeObjs(writer, list) {
        writer.WriteArrayStart();
        for (let value of list) {
            this.WriteRuntimeObject(writer, value);
        }
        writer.WriteArrayEnd();
    }
    static WriteIntDictionary(writer, dict) {
        writer.WriteObjectStart();
        for (let [key, value] of dict) {
            writer.WriteIntProperty(key, value);
        }
        writer.WriteObjectEnd();
    }
    static WriteRuntimeObject(writer, obj) {
        let container = (0, TypeAssertion_1.asOrNull)(obj, Container_1.Container);
        if (container) {
            this.WriteRuntimeContainer(writer, container);
            return;
        }
        let divert = (0, TypeAssertion_1.asOrNull)(obj, Divert_1.Divert);
        if (divert) {
            let divTypeKey = "->";
            if (divert.isExternal) {
                divTypeKey = "x()";
            }
            else if (divert.pushesToStack) {
                if (divert.stackPushType == PushPop_1.PushPopType.Function) {
                    divTypeKey = "f()";
                }
                else if (divert.stackPushType == PushPop_1.PushPopType.Tunnel) {
                    divTypeKey = "->t->";
                }
            }
            let targetStr;
            if (divert.hasVariableTarget) {
                targetStr = divert.variableDivertName;
            }
            else {
                targetStr = divert.targetPathString;
            }
            writer.WriteObjectStart();
            writer.WriteProperty(divTypeKey, targetStr);
            if (divert.hasVariableTarget) {
                writer.WriteProperty("var", true);
            }
            if (divert.isConditional) {
                writer.WriteProperty("c", true);
            }
            if (divert.externalArgs > 0) {
                writer.WriteIntProperty("exArgs", divert.externalArgs);
            }
            writer.WriteObjectEnd();
            return;
        }
        let choicePoint = (0, TypeAssertion_1.asOrNull)(obj, ChoicePoint_1.ChoicePoint);
        if (choicePoint) {
            writer.WriteObjectStart();
            writer.WriteProperty("*", choicePoint.pathStringOnChoice);
            writer.WriteIntProperty("flg", choicePoint.flags);
            writer.WriteObjectEnd();
            return;
        }
        let boolVal = (0, TypeAssertion_1.asOrNull)(obj, Value_1.BoolValue);
        if (boolVal) {
            writer.WriteBool(boolVal.value);
            return;
        }
        let intVal = (0, TypeAssertion_1.asOrNull)(obj, Value_1.IntValue);
        if (intVal) {
            writer.WriteInt(intVal.value);
            return;
        }
        let floatVal = (0, TypeAssertion_1.asOrNull)(obj, Value_1.FloatValue);
        if (floatVal) {
            writer.WriteFloat(floatVal.value);
            return;
        }
        let strVal = (0, TypeAssertion_1.asOrNull)(obj, Value_1.StringValue);
        if (strVal) {
            if (strVal.isNewline) {
                writer.Write("\n", false);
            }
            else {
                writer.WriteStringStart();
                writer.WriteStringInner("^");
                writer.WriteStringInner(strVal.value);
                writer.WriteStringEnd();
            }
            return;
        }
        let listVal = (0, TypeAssertion_1.asOrNull)(obj, Value_1.ListValue);
        if (listVal) {
            this.WriteInkList(writer, listVal);
            return;
        }
        let divTargetVal = (0, TypeAssertion_1.asOrNull)(obj, Value_1.DivertTargetValue);
        if (divTargetVal) {
            writer.WriteObjectStart();
            if (divTargetVal.value === null) {
                return (0, NullException_1.throwNullException)("divTargetVal.value");
            }
            writer.WriteProperty("^->", divTargetVal.value.componentsString);
            writer.WriteObjectEnd();
            return;
        }
        let varPtrVal = (0, TypeAssertion_1.asOrNull)(obj, Value_1.VariablePointerValue);
        if (varPtrVal) {
            writer.WriteObjectStart();
            writer.WriteProperty("^var", varPtrVal.value);
            writer.WriteIntProperty("ci", varPtrVal.contextIndex);
            writer.WriteObjectEnd();
            return;
        }
        let glue = (0, TypeAssertion_1.asOrNull)(obj, Glue_1.Glue);
        if (glue) {
            writer.Write("<>");
            return;
        }
        let controlCmd = (0, TypeAssertion_1.asOrNull)(obj, ControlCommand_1.ControlCommand);
        if (controlCmd) {
            writer.Write(JsonSerialisation._controlCommandNames[controlCmd.commandType]);
            return;
        }
        let nativeFunc = (0, TypeAssertion_1.asOrNull)(obj, NativeFunctionCall_1.NativeFunctionCall);
        if (nativeFunc) {
            let name = nativeFunc.name;
            if (name == "^")
                name = "L^";
            writer.Write(name);
            return;
        }
        let varRef = (0, TypeAssertion_1.asOrNull)(obj, VariableReference_1.VariableReference);
        if (varRef) {
            writer.WriteObjectStart();
            let readCountPath = varRef.pathStringForCount;
            if (readCountPath != null) {
                writer.WriteProperty("CNT?", readCountPath);
            }
            else {
                writer.WriteProperty("VAR?", varRef.name);
            }
            writer.WriteObjectEnd();
            return;
        }
        let varAss = (0, TypeAssertion_1.asOrNull)(obj, VariableAssignment_1.VariableAssignment);
        if (varAss) {
            writer.WriteObjectStart();
            let key = varAss.isGlobal ? "VAR=" : "temp=";
            writer.WriteProperty(key, varAss.variableName);
            // Reassignment?
            if (!varAss.isNewDeclaration)
                writer.WriteProperty("re", true);
            writer.WriteObjectEnd();
            return;
        }
        let voidObj = (0, TypeAssertion_1.asOrNull)(obj, Void_1.Void);
        if (voidObj) {
            writer.Write("void");
            return;
        }
        let tag = (0, TypeAssertion_1.asOrNull)(obj, Tag_1.Tag);
        if (tag) {
            writer.WriteObjectStart();
            writer.WriteProperty("#", tag.text);
            writer.WriteObjectEnd();
            return;
        }
        let choice = (0, TypeAssertion_1.asOrNull)(obj, Choice_1.Choice);
        if (choice) {
            this.WriteChoice(writer, choice);
            return;
        }
        throw new Error("Failed to convert runtime object to Json token: " + obj);
    }
    static JObjectToDictionaryRuntimeObjs(jObject) {
        let dict = new Map();
        for (let key in jObject) {
            if (jObject.hasOwnProperty(key)) {
                let inkObject = this.JTokenToRuntimeObject(jObject[key]);
                if (inkObject === null) {
                    return (0, NullException_1.throwNullException)("inkObject");
                }
                dict.set(key, inkObject);
            }
        }
        return dict;
    }
    static JObjectToIntDictionary(jObject) {
        let dict = new Map();
        for (let key in jObject) {
            if (jObject.hasOwnProperty(key)) {
                dict.set(key, parseInt(jObject[key]));
            }
        }
        return dict;
    }
    static JTokenToRuntimeObject(token) {
        if ((typeof token === "number" && !isNaN(token)) ||
            typeof token === "boolean") {
            return Value_1.Value.Create(token);
        }
        if (typeof token === "string") {
            let str = token.toString();
            //Explicit float value of the form "123.00f"
            const floatRepresentation = /^([0-9]+.[0-9]+f)$/.exec(str);
            if (floatRepresentation) {
                return new Value_1.FloatValue(parseFloat(floatRepresentation[0]));
            }
            // String value
            let firstChar = str[0];
            if (firstChar == "^")
                return new Value_1.StringValue(str.substring(1));
            else if (firstChar == "\n" && str.length == 1)
                return new Value_1.StringValue("\n");
            // Glue
            if (str == "<>")
                return new Glue_1.Glue();
            // Control commands (would looking up in a hash set be faster?)
            for (let i = 0; i < JsonSerialisation._controlCommandNames.length; ++i) {
                let cmdName = JsonSerialisation._controlCommandNames[i];
                if (str == cmdName) {
                    return new ControlCommand_1.ControlCommand(i);
                }
            }
            // Native functions
            if (str == "L^")
                str = "^";
            if (NativeFunctionCall_1.NativeFunctionCall.CallExistsWithName(str))
                return NativeFunctionCall_1.NativeFunctionCall.CallWithName(str);
            // Pop
            if (str == "->->")
                return ControlCommand_1.ControlCommand.PopTunnel();
            else if (str == "~ret")
                return ControlCommand_1.ControlCommand.PopFunction();
            // Void
            if (str == "void")
                return new Void_1.Void();
        }
        if (typeof token === "object" && !Array.isArray(token)) {
            let obj = token;
            let propValue;
            // Divert target value to path
            if (obj["^->"]) {
                propValue = obj["^->"];
                return new Value_1.DivertTargetValue(new Path_1.Path(propValue.toString()));
            }
            // VariablePointerValue
            if (obj["^var"]) {
                propValue = obj["^var"];
                let varPtr = new Value_1.VariablePointerValue(propValue.toString());
                if ("ci" in obj) {
                    propValue = obj["ci"];
                    varPtr.contextIndex = parseInt(propValue);
                }
                return varPtr;
            }
            // Divert
            let isDivert = false;
            let pushesToStack = false;
            let divPushType = PushPop_1.PushPopType.Function;
            let external = false;
            if ((propValue = obj["->"])) {
                isDivert = true;
            }
            else if ((propValue = obj["f()"])) {
                isDivert = true;
                pushesToStack = true;
                divPushType = PushPop_1.PushPopType.Function;
            }
            else if ((propValue = obj["->t->"])) {
                isDivert = true;
                pushesToStack = true;
                divPushType = PushPop_1.PushPopType.Tunnel;
            }
            else if ((propValue = obj["x()"])) {
                isDivert = true;
                external = true;
                pushesToStack = false;
                divPushType = PushPop_1.PushPopType.Function;
            }
            if (isDivert) {
                let divert = new Divert_1.Divert();
                divert.pushesToStack = pushesToStack;
                divert.stackPushType = divPushType;
                divert.isExternal = external;
                let target = propValue.toString();
                if ((propValue = obj["var"]))
                    divert.variableDivertName = target;
                else
                    divert.targetPathString = target;
                divert.isConditional = !!obj["c"];
                if (external) {
                    if ((propValue = obj["exArgs"]))
                        divert.externalArgs = parseInt(propValue);
                }
                return divert;
            }
            // Choice
            if ((propValue = obj["*"])) {
                let choice = new ChoicePoint_1.ChoicePoint();
                choice.pathStringOnChoice = propValue.toString();
                if ((propValue = obj["flg"]))
                    choice.flags = parseInt(propValue);
                return choice;
            }
            // Variable reference
            if ((propValue = obj["VAR?"])) {
                return new VariableReference_1.VariableReference(propValue.toString());
            }
            else if ((propValue = obj["CNT?"])) {
                let readCountVarRef = new VariableReference_1.VariableReference();
                readCountVarRef.pathStringForCount = propValue.toString();
                return readCountVarRef;
            }
            // Variable assignment
            let isVarAss = false;
            let isGlobalVar = false;
            if ((propValue = obj["VAR="])) {
                isVarAss = true;
                isGlobalVar = true;
            }
            else if ((propValue = obj["temp="])) {
                isVarAss = true;
                isGlobalVar = false;
            }
            if (isVarAss) {
                let varName = propValue.toString();
                let isNewDecl = !obj["re"];
                let varAss = new VariableAssignment_1.VariableAssignment(varName, isNewDecl);
                varAss.isGlobal = isGlobalVar;
                return varAss;
            }
            if (obj["#"] !== undefined) {
                propValue = obj["#"];
                return new Tag_1.Tag(propValue.toString());
            }
            // List value
            if ((propValue = obj["list"])) {
                // var listContent = (Dictionary<string, object>)propValue;
                let listContent = propValue;
                let rawList = new InkList_1.InkList();
                if ((propValue = obj["origins"])) {
                    // var namesAsObjs = (List<object>)propValue;
                    let namesAsObjs = propValue;
                    // rawList.SetInitialOriginNames(namesAsObjs.Cast<string>().ToList());
                    rawList.SetInitialOriginNames(namesAsObjs);
                }
                for (let key in listContent) {
                    if (listContent.hasOwnProperty(key)) {
                        let nameToVal = listContent[key];
                        let item = new InkList_1.InkListItem(key);
                        let val = parseInt(nameToVal);
                        rawList.Add(item, val);
                    }
                }
                return new Value_1.ListValue(rawList);
            }
            if (obj["originalChoicePath"] != null)
                return this.JObjectToChoice(obj);
        }
        // Array is always a Runtime.Container
        if (Array.isArray(token)) {
            return this.JArrayToContainer(token);
        }
        if (token === null || token === undefined)
            return null;
        throw new Error("Failed to convert token to runtime object: " +
            this.toJson(token, ["parent"]));
    }
    static toJson(me, removes, space) {
        return JSON.stringify(me, (k, v) => ((removes === null || removes === void 0 ? void 0 : removes.some((r) => r === k)) ? undefined : v), space);
    }
    static WriteRuntimeContainer(writer, container, withoutName = false) {
        writer.WriteArrayStart();
        if (container === null) {
            return (0, NullException_1.throwNullException)("container");
        }
        for (let c of container.content)
            this.WriteRuntimeObject(writer, c);
        let namedOnlyContent = container.namedOnlyContent;
        let countFlags = container.countFlags;
        let hasNameProperty = container.name != null && !withoutName;
        let hasTerminator = namedOnlyContent != null || countFlags > 0 || hasNameProperty;
        if (hasTerminator) {
            writer.WriteObjectStart();
        }
        if (namedOnlyContent != null) {
            for (let [key, value] of namedOnlyContent) {
                let name = key;
                let namedContainer = (0, TypeAssertion_1.asOrNull)(value, Container_1.Container);
                writer.WritePropertyStart(name);
                this.WriteRuntimeContainer(writer, namedContainer, true);
                writer.WritePropertyEnd();
            }
        }
        if (countFlags > 0)
            writer.WriteIntProperty("#f", countFlags);
        if (hasNameProperty)
            writer.WriteProperty("#n", container.name);
        if (hasTerminator)
            writer.WriteObjectEnd();
        else
            writer.WriteNull();
        writer.WriteArrayEnd();
    }
    static JArrayToContainer(jArray) {
        let container = new Container_1.Container();
        container.content = this.JArrayToRuntimeObjList(jArray, true);
        let terminatingObj = jArray[jArray.length - 1];
        if (terminatingObj != null) {
            let namedOnlyContent = new Map();
            for (let key in terminatingObj) {
                if (key == "#f") {
                    container.countFlags = parseInt(terminatingObj[key]);
                }
                else if (key == "#n") {
                    container.name = terminatingObj[key].toString();
                }
                else {
                    let namedContentItem = this.JTokenToRuntimeObject(terminatingObj[key]);
                    // var namedSubContainer = namedContentItem as Container;
                    let namedSubContainer = (0, TypeAssertion_1.asOrNull)(namedContentItem, Container_1.Container);
                    if (namedSubContainer)
                        namedSubContainer.name = key;
                    namedOnlyContent.set(key, namedContentItem);
                }
            }
            container.namedOnlyContent = namedOnlyContent;
        }
        return container;
    }
    static JObjectToChoice(jObj) {
        let choice = new Choice_1.Choice();
        choice.text = jObj["text"].toString();
        choice.index = parseInt(jObj["index"]);
        choice.sourcePath = jObj["originalChoicePath"].toString();
        choice.originalThreadIndex = parseInt(jObj["originalThreadIndex"]);
        choice.pathStringOnChoice = jObj["targetPath"].toString();
        choice.tags = this.JArrayToTags(jObj);
        choice.isInvisibleDefault = !!jObj["isInvisibleDefault"];
        return choice;
    }
    static JArrayToTags(jObj) {
        if (jObj["tags"]) {
            return jObj["tags"];
        }
        else {
            return null;
        }
    }
    static WriteChoice(writer, choice) {
        writer.WriteObjectStart();
        writer.WriteProperty("text", choice.text);
        writer.WriteIntProperty("index", choice.index);
        writer.WriteProperty("originalChoicePath", choice.sourcePath);
        writer.WriteIntProperty("originalThreadIndex", choice.originalThreadIndex);
        writer.WriteProperty("targetPath", choice.pathStringOnChoice);
        writer.WriteProperty("isInvisibleDefault", choice.isInvisibleDefault);
        this.WriteChoiceTags(writer, choice);
        writer.WriteObjectEnd();
    }
    static WriteChoiceTags(writer, choice) {
        if (choice.tags && choice.tags.length > 0) {
            writer.WritePropertyStart("tags");
            writer.WriteArrayStart();
            for (const tag of choice.tags) {
                writer.Write(tag);
            }
            writer.WriteArrayEnd();
            writer.WritePropertyEnd();
        }
    }
    static WriteInkList(writer, listVal) {
        let rawList = listVal.value;
        if (rawList === null) {
            return (0, NullException_1.throwNullException)("rawList");
        }
        writer.WriteObjectStart();
        writer.WritePropertyStart("list");
        writer.WriteObjectStart();
        for (let [key, val] of rawList) {
            let item = InkList_1.InkListItem.fromSerializedKey(key);
            let itemVal = val;
            if (item.itemName === null) {
                return (0, NullException_1.throwNullException)("item.itemName");
            }
            writer.WritePropertyNameStart();
            writer.WritePropertyNameInner(item.originName ? item.originName : "?");
            writer.WritePropertyNameInner(".");
            writer.WritePropertyNameInner(item.itemName);
            writer.WritePropertyNameEnd();
            writer.Write(itemVal);
            writer.WritePropertyEnd();
        }
        writer.WriteObjectEnd();
        writer.WritePropertyEnd();
        if (rawList.Count == 0 &&
            rawList.originNames != null &&
            rawList.originNames.length > 0) {
            writer.WritePropertyStart("origins");
            writer.WriteArrayStart();
            for (let name of rawList.originNames)
                writer.Write(name);
            writer.WriteArrayEnd();
            writer.WritePropertyEnd();
        }
        writer.WriteObjectEnd();
    }
    static ListDefinitionsToJToken(origin) {
        let result = {};
        for (let def of origin.lists) {
            let listDefJson = {};
            for (let [key, val] of def.items) {
                let item = InkList_1.InkListItem.fromSerializedKey(key);
                if (item.itemName === null) {
                    return (0, NullException_1.throwNullException)("item.itemName");
                }
                listDefJson[item.itemName] = val;
            }
            result[def.name] = listDefJson;
        }
        return result;
    }
    static JTokenToListDefinitions(obj) {
        // var defsObj = (Dictionary<string, object>)obj;
        let defsObj = obj;
        let allDefs = [];
        for (let key in defsObj) {
            if (defsObj.hasOwnProperty(key)) {
                let name = key.toString();
                // var listDefJson = (Dictionary<string, object>)kv.Value;
                let listDefJson = defsObj[key];
                // Cast (string, object) to (string, int) for items
                let items = new Map();
                for (let nameValueKey in listDefJson) {
                    if (defsObj.hasOwnProperty(key)) {
                        let nameValue = listDefJson[nameValueKey];
                        items.set(nameValueKey, parseInt(nameValue));
                    }
                }
                let def = new ListDefinition_1.ListDefinition(name, items);
                allDefs.push(def);
            }
        }
        return new ListDefinitionsOrigin_1.ListDefinitionsOrigin(allDefs);
    }
}
exports.JsonSerialisation = JsonSerialisation;
JsonSerialisation._controlCommandNames = (() => {
    let _controlCommandNames = [];
    _controlCommandNames[ControlCommand_1.ControlCommand.CommandType.EvalStart] = "ev";
    _controlCommandNames[ControlCommand_1.ControlCommand.CommandType.EvalOutput] = "out";
    _controlCommandNames[ControlCommand_1.ControlCommand.CommandType.EvalEnd] = "/ev";
    _controlCommandNames[ControlCommand_1.ControlCommand.CommandType.Duplicate] = "du";
    _controlCommandNames[ControlCommand_1.ControlCommand.CommandType.PopEvaluatedValue] = "pop";
    _controlCommandNames[ControlCommand_1.ControlCommand.CommandType.PopFunction] = "~ret";
    _controlCommandNames[ControlCommand_1.ControlCommand.CommandType.PopTunnel] = "->->";
    _controlCommandNames[ControlCommand_1.ControlCommand.CommandType.BeginString] = "str";
    _controlCommandNames[ControlCommand_1.ControlCommand.CommandType.EndString] = "/str";
    _controlCommandNames[ControlCommand_1.ControlCommand.CommandType.NoOp] = "nop";
    _controlCommandNames[ControlCommand_1.ControlCommand.CommandType.ChoiceCount] = "choiceCnt";
    _controlCommandNames[ControlCommand_1.ControlCommand.CommandType.Turns] = "turn";
    _controlCommandNames[ControlCommand_1.ControlCommand.CommandType.TurnsSince] = "turns";
    _controlCommandNames[ControlCommand_1.ControlCommand.CommandType.ReadCount] = "readc";
    _controlCommandNames[ControlCommand_1.ControlCommand.CommandType.Random] = "rnd";
    _controlCommandNames[ControlCommand_1.ControlCommand.CommandType.SeedRandom] = "srnd";
    _controlCommandNames[ControlCommand_1.ControlCommand.CommandType.VisitIndex] = "visit";
    _controlCommandNames[ControlCommand_1.ControlCommand.CommandType.SequenceShuffleIndex] =
        "seq";
    _controlCommandNames[ControlCommand_1.ControlCommand.CommandType.StartThread] = "thread";
    _controlCommandNames[ControlCommand_1.ControlCommand.CommandType.Done] = "done";
    _controlCommandNames[ControlCommand_1.ControlCommand.CommandType.End] = "end";
    _controlCommandNames[ControlCommand_1.ControlCommand.CommandType.ListFromInt] = "listInt";
    _controlCommandNames[ControlCommand_1.ControlCommand.CommandType.ListRange] = "range";
    _controlCommandNames[ControlCommand_1.ControlCommand.CommandType.ListRandom] = "lrnd";
    _controlCommandNames[ControlCommand_1.ControlCommand.CommandType.BeginTag] = "#";
    _controlCommandNames[ControlCommand_1.ControlCommand.CommandType.EndTag] = "/#";
    for (let i = 0; i < ControlCommand_1.ControlCommand.CommandType.TOTAL_VALUES; ++i) {
        if (_controlCommandNames[i] == null)
            throw new Error("Control command not accounted for in serialisation");
    }
    return _controlCommandNames;
})();
//# sourceMappingURL=JsonSerialisation.js.map