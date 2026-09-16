"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.FunctionCall = void 0;
const ControlCommand_1 = require("../../../engine/ControlCommand");
const Divert_1 = require("./Divert/Divert");
const DivertTarget_1 = require("./Divert/DivertTarget");
const Expression_1 = require("./Expression/Expression");
const InkList_1 = require("../../../engine/InkList");
const Value_1 = require("../../../engine/Value");
const NativeFunctionCall_1 = require("../../../engine/NativeFunctionCall");
const NumberExpression_1 = require("./Expression/NumberExpression");
const Path_1 = require("./Path");
const Value_2 = require("../../../engine/Value");
const VariableReference_1 = require("./Variable/VariableReference");
const TypeAssertion_1 = require("../../../engine/TypeAssertion");
class FunctionCall extends Expression_1.Expression {
    get proxyDivert() {
        return this._proxyDivert;
    }
    get name() {
        return this._proxyDivert.target.firstComponent || "";
    }
    get args() {
        return this._proxyDivert.args;
    }
    get runtimeDivert() {
        return this._proxyDivert.runtimeDivert;
    }
    get isChoiceCount() {
        return this.name === "CHOICE_COUNT";
    }
    get isTurns() {
        return this.name === "TURNS";
    }
    get isTurnsSince() {
        return this.name === "TURNS_SINCE";
    }
    get isRandom() {
        return this.name === "RANDOM";
    }
    get isSeedRandom() {
        return this.name === "SEED_RANDOM";
    }
    get isListRange() {
        return this.name === "LIST_RANGE";
    }
    get isListRandom() {
        return this.name === "LIST_RANDOM";
    }
    get isReadCount() {
        return this.name === "READ_COUNT";
    }
    constructor(functionName, args) {
        super();
        this._divertTargetToCount = null;
        this._variableReferenceToCount = null;
        this.shouldPopReturnedValue = false;
        this.GenerateIntoContainer = (container) => {
            const foundList = this.story.ResolveList(this.name);
            let usingProxyDivert = false;
            if (this.isChoiceCount) {
                if (this.args.length > 0) {
                    this.Error("The CHOICE_COUNT() function shouldn't take any arguments");
                }
                container.AddContent(ControlCommand_1.ControlCommand.ChoiceCount());
            }
            else if (this.isTurns) {
                if (this.args.length > 0) {
                    this.Error("The TURNS() function shouldn't take any arguments");
                }
                container.AddContent(ControlCommand_1.ControlCommand.Turns());
            }
            else if (this.isTurnsSince || this.isReadCount) {
                const divertTarget = (0, TypeAssertion_1.asOrNull)(this.args[0], DivertTarget_1.DivertTarget);
                const variableDivertTarget = (0, TypeAssertion_1.asOrNull)(this.args[0], VariableReference_1.VariableReference);
                if (this.args.length !== 1 ||
                    (divertTarget === null && variableDivertTarget === null)) {
                    this.Error(`The ${this.name}() function should take one argument: a divert target to the target knot, stitch, gather or choice you want to check. e.g. TURNS_SINCE(-> myKnot)`);
                    return;
                }
                if (divertTarget) {
                    this._divertTargetToCount = divertTarget;
                    this.AddContent(this._divertTargetToCount);
                    this._divertTargetToCount.GenerateIntoContainer(container);
                }
                else if (variableDivertTarget) {
                    this._variableReferenceToCount = variableDivertTarget;
                    this.AddContent(this._variableReferenceToCount);
                    this._variableReferenceToCount.GenerateIntoContainer(container);
                }
                if (this.isTurnsSince) {
                    container.AddContent(ControlCommand_1.ControlCommand.TurnsSince());
                }
                else {
                    container.AddContent(ControlCommand_1.ControlCommand.ReadCount());
                }
            }
            else if (this.isRandom) {
                if (this.args.length !== 2) {
                    this.Error("RANDOM should take 2 parameters: a minimum and a maximum integer");
                }
                // We can type check single values, but not complex expressions
                for (let ii = 0; ii < this.args.length; ii += 1) {
                    const num = (0, TypeAssertion_1.asOrNull)(this.args[ii], NumberExpression_1.NumberExpression);
                    if (num && !num.isInt()) {
                        const paramName = ii === 0 ? "minimum" : "maximum";
                        this.Error(`RANDOM's ${paramName} parameter should be an integer`);
                    }
                    this.args[ii].GenerateIntoContainer(container);
                }
                container.AddContent(ControlCommand_1.ControlCommand.Random());
            }
            else if (this.isSeedRandom) {
                if (this.args.length !== 1) {
                    this.Error("SEED_RANDOM should take 1 parameter - an integer seed");
                }
                const num = (0, TypeAssertion_1.asOrNull)(this.args[0], NumberExpression_1.NumberExpression);
                if (num && !num.isInt()) {
                    this.Error("SEED_RANDOM's parameter should be an integer seed");
                }
                this.args[0].GenerateIntoContainer(container);
                container.AddContent(ControlCommand_1.ControlCommand.SeedRandom());
            }
            else if (this.isListRange) {
                if (this.args.length !== 3) {
                    this.Error("LIST_RANGE should take 3 parameters - a list, a min and a max");
                }
                for (let ii = 0; ii < this.args.length; ii += 1) {
                    this.args[ii].GenerateIntoContainer(container);
                }
                container.AddContent(ControlCommand_1.ControlCommand.ListRange());
            }
            else if (this.isListRandom) {
                if (this.args.length !== 1) {
                    this.Error("LIST_RANDOM should take 1 parameter - a list");
                }
                this.args[0].GenerateIntoContainer(container);
                container.AddContent(ControlCommand_1.ControlCommand.ListRandom());
            }
            else if (NativeFunctionCall_1.NativeFunctionCall.CallExistsWithName(this.name)) {
                const nativeCall = NativeFunctionCall_1.NativeFunctionCall.CallWithName(this.name);
                if (nativeCall.numberOfParameters !== this.args.length) {
                    let msg = `${FunctionCall.name} should take ${nativeCall.numberOfParameters} parameter`;
                    if (nativeCall.numberOfParameters > 1) {
                        msg += "s";
                    }
                    this.Error(msg);
                }
                for (let ii = 0; ii < this.args.length; ii += 1) {
                    this.args[ii].GenerateIntoContainer(container);
                }
                container.AddContent(NativeFunctionCall_1.NativeFunctionCall.CallWithName(this.name));
            }
            else if (foundList !== null) {
                if (this.args.length > 1) {
                    this.Error("Can currently only construct a list from one integer (or an empty list from a given list definition)");
                }
                // List item from given int
                if (this.args.length === 1) {
                    container.AddContent(new Value_2.StringValue(this.name));
                    this.args[0].GenerateIntoContainer(container);
                    container.AddContent(ControlCommand_1.ControlCommand.ListFromInt());
                }
                else {
                    // Empty list with given origin.
                    const list = new InkList_1.InkList();
                    list.SetInitialOriginName(this.name);
                    container.AddContent(new Value_1.ListValue(list));
                }
            }
            else {
                // Normal function call
                container.AddContent(this._proxyDivert.runtimeObject);
                usingProxyDivert = true;
            }
            // Don't attempt to resolve as a divert if we're not doing a normal function call
            if (!usingProxyDivert) {
                this.content.splice(this.content.indexOf(this._proxyDivert), 1);
            }
            // Function calls that are used alone on a tilda-based line:
            //  ~ func()
            // Should tidy up any returned value from the evaluation stack,
            // since it's unused.
            if (this.shouldPopReturnedValue) {
                container.AddContent(ControlCommand_1.ControlCommand.PopEvaluatedValue());
            }
        };
        this.toString = () => {
            const strArgs = this.args.join(", ");
            return `${this.name}(${strArgs})`;
        };
        this._proxyDivert = new Divert_1.Divert(new Path_1.Path(functionName), args);
        this._proxyDivert.isFunctionCall = true;
        this.AddContent(this._proxyDivert);
    }
    get typeName() {
        return "FunctionCall";
    }
    ResolveReferences(context) {
        super.ResolveReferences(context);
        // If we aren't using the proxy divert after all (e.g. if
        // it's a native function call), but we still have arguments,
        // we need to make sure they get resolved since the proxy divert
        // is no longer in the content array.
        if (!this.content.includes(this._proxyDivert) && this.args !== null) {
            for (const arg of this.args) {
                arg.ResolveReferences(context);
            }
        }
        if (this._divertTargetToCount) {
            const divert = this._divertTargetToCount.divert;
            const attemptingTurnCountOfVariableTarget = divert.runtimeDivert.variableDivertName != null;
            if (attemptingTurnCountOfVariableTarget) {
                this.Error(`When getting the TURNS_SINCE() of a variable target, remove the '->' - i.e. it should just be TURNS_SINCE(${divert.runtimeDivert.variableDivertName})`);
                return;
            }
            const targetObject = divert.targetContent;
            if (targetObject === null) {
                if (!attemptingTurnCountOfVariableTarget) {
                    this.Error(`Failed to find target for TURNS_SINCE: '${divert.target}'`);
                }
            }
            else {
                if (!targetObject.containerForCounting) {
                    throw new Error();
                }
                targetObject.containerForCounting.turnIndexShouldBeCounted = true;
            }
        }
        else if (this._variableReferenceToCount) {
            const runtimeVarRef = this._variableReferenceToCount.runtimeVarRef;
            if (!runtimeVarRef) {
                throw new Error();
            }
            if (runtimeVarRef.pathForCount !== null) {
                this.Error(`Should be '${FunctionCall.name}'(-> '${this._variableReferenceToCount.name}). Usage without the '->' only makes sense for variable targets.`);
            }
        }
    }
}
exports.FunctionCall = FunctionCall;
FunctionCall.IsBuiltIn = (name) => {
    if (NativeFunctionCall_1.NativeFunctionCall.CallExistsWithName(name)) {
        return true;
    }
    return (name === "CHOICE_COUNT" ||
        name === "TURNS_SINCE" ||
        name === "TURNS" ||
        name === "RANDOM" ||
        name === "SEED_RANDOM" ||
        name === "LIST_VALUE" ||
        name === "LIST_RANDOM" ||
        name === "READ_COUNT");
};
//# sourceMappingURL=FunctionCall.js.map