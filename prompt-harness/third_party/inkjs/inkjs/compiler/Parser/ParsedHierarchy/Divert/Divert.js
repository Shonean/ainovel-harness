"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.Divert = void 0;
const Container_1 = require("../../../../engine/Container");
const ControlCommand_1 = require("../../../../engine/ControlCommand");
const Divert_1 = require("../../../../engine/Divert");
const DivertTarget_1 = require("./DivertTarget");
const FlowBase_1 = require("../Flow/FlowBase");
const FunctionCall_1 = require("../FunctionCall");
const Object_1 = require("../Object");
const Path_1 = require("../Path");
const Path_2 = require("../../../../engine/Path");
const PushPop_1 = require("../../../../engine/PushPop");
const Value_1 = require("../../../../engine/Value");
const VariableReference_1 = require("../Variable/VariableReference");
const ClosestFlowBase_1 = require("../Flow/ClosestFlowBase");
const TypeAssertion_1 = require("../../../../engine/TypeAssertion");
class Divert extends Object_1.ParsedObject {
    get runtimeDivert() {
        if (!this._runtimeDivert) {
            throw new Error();
        }
        return this._runtimeDivert;
    }
    set runtimeDivert(value) {
        this._runtimeDivert = value;
    }
    get isEnd() {
        return Boolean(this.target && this.target.dotSeparatedComponents === "END");
    }
    get isDone() {
        return Boolean(this.target && this.target.dotSeparatedComponents === "DONE");
    }
    constructor(target, args) {
        super();
        this.args = [];
        this.target = null;
        this.targetContent = null;
        this._runtimeDivert = null;
        this.isFunctionCall = false;
        this.isEmpty = false;
        this.isTunnel = false;
        this.isThread = false;
        this.GenerateRuntimeObject = () => {
            // End = end flow immediately
            // Done = return from thread or instruct the flow that it's safe to exit
            if (this.isEnd) {
                return ControlCommand_1.ControlCommand.End();
            }
            else if (this.isDone) {
                return ControlCommand_1.ControlCommand.Done();
            }
            this.runtimeDivert = new Divert_1.Divert();
            // Normally we resolve the target content during the
            // Resolve phase, since we expect all runtime objects to
            // be available in order to find the final runtime path for
            // the destination. However, we need to resolve the target
            // (albeit without the runtime target) early so that
            // we can get information about the arguments - whether
            // they're by reference - since it affects the code we
            // generate here.
            this.ResolveTargetContent();
            this.CheckArgumentValidity();
            // Passing arguments to the knot
            const requiresArgCodeGen = this.args !== null && this.args.length > 0;
            if (requiresArgCodeGen ||
                this.isFunctionCall ||
                this.isTunnel ||
                this.isThread) {
                const container = new Container_1.Container();
                // Generate code for argument evaluation
                // This argument generation is coded defensively - it should
                // attempt to generate the code for all the parameters, even if
                // they don't match the expected arguments. This is so that the
                // parameter objects themselves are generated correctly and don't
                // get into a state of attempting to resolve references etc
                // without being generated.
                if (requiresArgCodeGen) {
                    // Function calls already in an evaluation context
                    if (!this.isFunctionCall) {
                        container.AddContent(ControlCommand_1.ControlCommand.EvalStart());
                    }
                    let targetArguments = null;
                    if (this.targetContent) {
                        targetArguments = this.targetContent.args;
                    }
                    for (let ii = 0; ii < this.args.length; ++ii) {
                        const argToPass = this.args[ii];
                        let argExpected = null;
                        if (targetArguments && ii < targetArguments.length) {
                            argExpected = targetArguments[ii];
                        }
                        // Pass by reference: argument needs to be a variable reference
                        if (argExpected && argExpected.isByReference) {
                            const varRef = (0, TypeAssertion_1.asOrNull)(argToPass, VariableReference_1.VariableReference);
                            if (!varRef) {
                                this.Error(`Expected variable name to pass by reference to 'ref ${argExpected.identifier}' but saw ${argToPass}`);
                                break;
                            }
                            // Check that we're not attempting to pass a read count by reference
                            const targetPath = new Path_1.Path(varRef.pathIdentifiers);
                            const targetForCount = targetPath.ResolveFromContext(this);
                            if (targetForCount) {
                                this.Error(`can't pass a read count by reference. '${targetPath.dotSeparatedComponents}' is a knot/stitch/label, but '${this.target.dotSeparatedComponents}' requires the name of a VAR to be passed.`);
                                break;
                            }
                            const varPointer = new Value_1.VariablePointerValue(varRef.name);
                            container.AddContent(varPointer);
                        }
                        else {
                            // Normal value being passed: evaluate it as normal
                            argToPass.GenerateIntoContainer(container);
                        }
                    }
                    // Function calls were already in an evaluation context
                    if (!this.isFunctionCall) {
                        container.AddContent(ControlCommand_1.ControlCommand.EvalEnd());
                    }
                }
                // Starting a thread? A bit like a push to the call stack below... but not.
                // It sort of puts the call stack on a thread stack (argh!) - forks the full flow.
                if (this.isThread) {
                    container.AddContent(ControlCommand_1.ControlCommand.StartThread());
                }
                else if (this.isFunctionCall || this.isTunnel) {
                    // If this divert is a function call, tunnel, we push to the call stack
                    // so we can return again
                    this.runtimeDivert.pushesToStack = true;
                    this.runtimeDivert.stackPushType = this.isFunctionCall
                        ? PushPop_1.PushPopType.Function
                        : PushPop_1.PushPopType.Tunnel;
                }
                // Jump into the "function" (knot/stitch)
                container.AddContent(this.runtimeDivert);
                return container;
            }
            // Simple divert
            return this.runtimeDivert;
        };
        // When the divert is to a target that's actually a variable name
        // rather than an explicit knot/stitch name, try interpretting it
        // as such by getting the variable name.
        this.PathAsVariableName = () => this.target ? this.target.firstComponent : null;
        this.ResolveTargetContent = () => {
            if (this.isEmpty || this.isEnd) {
                return;
            }
            if (this.targetContent === null) {
                // Is target of this divert a variable name that will be de-referenced
                // at runtime? If so, there won't be any further reference resolution
                // we can do at this point.
                let variableTargetName = this.PathAsVariableName();
                if (variableTargetName !== null) {
                    const flowBaseScope = (0, TypeAssertion_1.asOrNull)((0, ClosestFlowBase_1.ClosestFlowBase)(this), FlowBase_1.FlowBase);
                    if (flowBaseScope) {
                        const resolveResult = flowBaseScope.ResolveVariableWithName(variableTargetName, this);
                        if (resolveResult.found) {
                            // Make sure that the flow was typed correctly, given that we know that this
                            // is meant to be a divert target
                            if (resolveResult.isArgument &&
                                resolveResult.ownerFlow &&
                                resolveResult.ownerFlow.args) {
                                let argument = resolveResult.ownerFlow.args.find((a) => { var _a; return ((_a = a.identifier) === null || _a === void 0 ? void 0 : _a.name) == variableTargetName; });
                                if (argument && !argument.isDivertTarget) {
                                    this.Error(`Since '${argument.identifier}' is used as a variable divert target (on ${this.debugMetadata}), it should be marked as: -> ${argument.identifier}`, resolveResult.ownerFlow);
                                }
                            }
                            this.runtimeDivert.variableDivertName = variableTargetName;
                            return;
                        }
                    }
                }
                if (!this.target) {
                    throw new Error();
                }
                this.targetContent = this.target.ResolveFromContext(this);
            }
        };
        // Returns false if there's an error
        this.CheckArgumentValidity = () => {
            if (this.isEmpty) {
                return;
            }
            // Argument passing: Check for errors in number of arguments
            let numArgs = 0;
            if (this.args !== null && this.args.length > 0) {
                numArgs = this.args.length;
            }
            // Missing content?
            // Can't check arguments properly. It'll be due to some
            // other error though, so although there's a problem and
            // we report false, we don't need to report a specific error.
            // It may also be because it's a valid call to an external
            // function, that we check at the resolve stage.
            if (this.targetContent === null) {
                return;
            }
            const targetFlow = (0, TypeAssertion_1.asOrNull)(this.targetContent, FlowBase_1.FlowBase);
            // No error, crikey!
            if (numArgs === 0 && (targetFlow === null || !targetFlow.hasParameters)) {
                return;
            }
            else if (targetFlow === null && numArgs > 0) {
                this.Error("target needs to be a knot or stitch in order to pass arguments");
                return;
            }
            else if (targetFlow !== null &&
                (targetFlow.args === null || (!targetFlow.args && numArgs > 0))) {
                this.Error(`target (${targetFlow.name}) doesn't take parameters`);
                return;
            }
            else if (this.parent instanceof DivertTarget_1.DivertTarget) {
                if (numArgs > 0) {
                    this.Error(`can't store arguments in a divert target variable`);
                }
                return;
            }
            const paramCount = targetFlow.args.length;
            if (paramCount !== numArgs) {
                let butClause;
                if (numArgs === 0) {
                    butClause = "but there weren't any passed to it";
                }
                else if (numArgs < paramCount) {
                    butClause = `but only got ${numArgs}`;
                }
                else {
                    butClause = `but got ${numArgs}`;
                }
                this.Error(`to '${targetFlow.identifier}' requires ${paramCount} arguments, ${butClause}`);
                return;
            }
            // Light type-checking for divert target arguments
            for (let ii = 0; ii < paramCount; ++ii) {
                const flowArg = targetFlow.args[ii];
                const divArgExpr = this.args[ii];
                // Expecting a divert target as an argument, let's do some basic type checking
                if (flowArg.isDivertTarget) {
                    // Not passing a divert target or any kind of variable reference?
                    let varRef = (0, TypeAssertion_1.asOrNull)(divArgExpr, VariableReference_1.VariableReference);
                    if (!(divArgExpr instanceof DivertTarget_1.DivertTarget) && varRef === null) {
                        this.Error(`Target '${targetFlow.identifier}' expects a divert target for the parameter named -> ${flowArg.identifier} but saw ${divArgExpr}`, divArgExpr);
                    }
                    else if (varRef) {
                        // Passing 'a' instead of '-> a'?
                        // i.e. read count instead of divert target
                        // Unfortunately have to manually resolve here since we're still in code gen
                        const knotCountPath = new Path_1.Path(varRef.pathIdentifiers);
                        const targetForCount = knotCountPath.ResolveFromContext(varRef);
                        if (targetForCount) {
                            this.Error(`Passing read count of '${knotCountPath.dotSeparatedComponents}' instead of a divert target. You probably meant '${knotCountPath}'`);
                        }
                    }
                }
            }
            if (targetFlow === null) {
                this.Error("Can't call as a function or with arguments unless it's a knot or stitch");
                return;
            }
            return;
        };
        this.CheckExternalArgumentValidity = (context) => {
            const externalName = this.target
                ? this.target.firstComponent
                : null;
            const external = context.externals.get(externalName);
            if (!external) {
                throw new Error("external not found");
            }
            const externalArgCount = external.argumentNames.length;
            let ownArgCount = 0;
            if (this.args) {
                ownArgCount = this.args.length;
            }
            if (ownArgCount !== externalArgCount) {
                this.Error(`incorrect number of arguments sent to external function '${externalName}'. Expected ${externalArgCount} but got ${ownArgCount}`);
            }
        };
        this.toString = () => {
            let returnString = "";
            if (this.target !== null) {
                returnString += this.target.toString();
            }
            else {
                return "-> <empty divert>";
            }
            if (this.isTunnel) {
                returnString += " ->";
            }
            if (this.isFunctionCall) {
                returnString += " ()";
            }
            return returnString;
        };
        if (target) {
            this.target = target;
        }
        if (args) {
            this.args = args;
            this.AddContent(args);
        }
    }
    get typeName() {
        return "Divert";
    }
    ResolveReferences(context) {
        if (this.isEmpty || this.isEnd || this.isDone) {
            return;
        }
        else if (!this.runtimeDivert) {
            throw new Error();
        }
        if (this.targetContent) {
            this.runtimeDivert.targetPath = this.targetContent.runtimePath;
        }
        // Resolve children (the arguments)
        super.ResolveReferences(context);
        // May be null if it's a built in function (e.g. TURNS_SINCE)
        // or if it's a variable target.
        let targetFlow = (0, TypeAssertion_1.asOrNull)(this.targetContent, FlowBase_1.FlowBase);
        if (targetFlow) {
            if (!targetFlow.isFunction && this.isFunctionCall) {
                super.Error(`${targetFlow.identifier} hasn't been marked as a function, but it's being called as one. Do you need to declare the knot as '== function ${targetFlow.identifier} =='?`);
            }
            else if (targetFlow.isFunction &&
                !this.isFunctionCall &&
                !(this.parent instanceof DivertTarget_1.DivertTarget)) {
                super.Error(targetFlow.identifier +
                    " can't be diverted to. It can only be called as a function since it's been marked as such: '" +
                    targetFlow.identifier +
                    "(...)'");
            }
        }
        // Check validity of target content
        const targetWasFound = this.targetContent !== null;
        let isBuiltIn = false;
        let isExternal = false;
        if (!this.target) {
            throw new Error();
        }
        else if (this.target.numberOfComponents === 1) {
            if (!this.target.firstComponent) {
                throw new Error();
            }
            // BuiltIn means TURNS_SINCE, CHOICE_COUNT, RANDOM or SEED_RANDOM
            isBuiltIn = FunctionCall_1.FunctionCall.IsBuiltIn(this.target.firstComponent);
            // Client-bound function?
            isExternal = context.IsExternal(this.target.firstComponent);
            if (isBuiltIn || isExternal) {
                if (!this.isFunctionCall) {
                    super.Error(`${this.target.firstComponent} must be called as a function: ~ ${this.target.firstComponent}()`);
                }
                if (isExternal) {
                    this.runtimeDivert.isExternal = true;
                    if (this.args !== null) {
                        this.runtimeDivert.externalArgs = this.args.length;
                    }
                    this.runtimeDivert.pushesToStack = false;
                    this.runtimeDivert.targetPath = new Path_2.Path(this.target.firstComponent);
                    this.CheckExternalArgumentValidity(context);
                }
                return;
            }
        }
        // Variable target?
        if (this.runtimeDivert.variableDivertName != null) {
            return;
        }
        if (!targetWasFound && !isBuiltIn && !isExternal) {
            this.Error(`target not found: '${this.target}'`);
        }
    }
    Error(message, source = null, isWarning = false) {
        // Could be getting an error from a nested Divert
        if (source !== this && source) {
            super.Error(message, source);
            return;
        }
        if (this.isFunctionCall) {
            super.Error(`Function call ${message}`, source, isWarning);
        }
        else {
            super.Error(`Divert ${message}`, source, isWarning);
        }
    }
}
exports.Divert = Divert;
//# sourceMappingURL=Divert.js.map