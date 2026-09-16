"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.FlowBase = void 0;
const Choice_1 = require("../Choice");
const Divert_1 = require("../Divert/Divert");
const DivertTarget_1 = require("../Divert/DivertTarget");
const FlowLevel_1 = require("./FlowLevel");
const Gather_1 = require("../Gather/Gather");
// import { Knot } from '../Knot';
const Object_1 = require("../Object");
const Path_1 = require("../Path");
const ReturnType_1 = require("../ReturnType");
const Container_1 = require("../../../../engine/Container");
const Divert_2 = require("../../../../engine/Divert");
const VariableAssignment_1 = require("../../../../engine/VariableAssignment");
//import { Story } from '../Story';
const SymbolType_1 = require("../SymbolType");
const Weave_1 = require("../Weave");
const ClosestFlowBase_1 = require("./ClosestFlowBase");
const Identifier_1 = require("../Identifier");
const TypeAssertion_1 = require("../../../../engine/TypeAssertion");
// Base class for Knots and Stitches
class FlowBase extends Object_1.ParsedObject {
    get hasParameters() {
        return this.args !== null && this.args.length > 0;
    }
    get subFlowsByName() {
        return this._subFlowsByName;
    }
    get typeName() {
        if (this.isFunction) {
            return "Function";
        }
        return String(this.flowLevel);
    }
    get name() {
        var _a;
        return ((_a = this.identifier) === null || _a === void 0 ? void 0 : _a.name) || null;
    }
    constructor(identifier, topLevelObjects = null, args = null, isFunction = false, isIncludedStory = false) {
        super();
        this.isFunction = isFunction;
        this._rootWeave = null;
        this._subFlowsByName = new Map();
        this._startingSubFlowDivert = null;
        this._startingSubFlowRuntime = null;
        this._firstChildFlow = null;
        this.variableDeclarations = new Map();
        this.identifier = null;
        this.args = null;
        this.iamFlowbase = () => true;
        this.SplitWeaveAndSubFlowContent = (contentObjs, isRootStory) => {
            var _a, _b;
            const weaveObjs = [];
            const subFlowObjs = [];
            this._subFlowsByName = new Map();
            for (const obj of contentObjs) {
                const subFlow = (0, TypeAssertion_1.asOrNull)(obj, FlowBase);
                if (subFlow) {
                    if (this._firstChildFlow === null) {
                        this._firstChildFlow = subFlow;
                    }
                    subFlowObjs.push(obj);
                    if ((_a = subFlow.identifier) === null || _a === void 0 ? void 0 : _a.name) {
                        this._subFlowsByName.set((_b = subFlow.identifier) === null || _b === void 0 ? void 0 : _b.name, subFlow);
                    }
                }
                else {
                    weaveObjs.push(obj);
                }
            }
            // Implicit final gather in top level story for ending without warning that you run out of content
            if (isRootStory) {
                weaveObjs.push(new Gather_1.Gather(null, 1), new Divert_1.Divert(new Path_1.Path(Identifier_1.Identifier.Done())));
            }
            const finalContent = [];
            if (weaveObjs.length > 0) {
                this._rootWeave = new Weave_1.Weave(weaveObjs, 0);
                finalContent.push(this._rootWeave);
            }
            if (subFlowObjs.length > 0) {
                finalContent.push(...subFlowObjs);
            }
            return finalContent;
        };
        this.ResolveVariableWithName = (varName, fromNode) => {
            var _a;
            const result = {};
            // Search in the stitch / knot that owns the node first
            const ownerFlow = fromNode === null ? this : (0, ClosestFlowBase_1.ClosestFlowBase)(fromNode);
            if (ownerFlow) {
                // Argument
                if (ownerFlow.args !== null) {
                    for (const arg of ownerFlow.args) {
                        if (((_a = arg.identifier) === null || _a === void 0 ? void 0 : _a.name) === varName) {
                            result.found = true;
                            result.isArgument = true;
                            result.ownerFlow = ownerFlow;
                            return result;
                        }
                    }
                }
                // Temp
                if (ownerFlow !== this.story &&
                    ownerFlow.variableDeclarations.has(varName)) {
                    result.found = true;
                    result.ownerFlow = ownerFlow;
                    result.isTemporary = true;
                    return result;
                }
            }
            // Global
            if (this.story.variableDeclarations.has(varName)) {
                result.found = true;
                result.ownerFlow = this.story;
                result.isGlobal = true;
                return result;
            }
            result.found = false;
            return result;
        };
        this.AddNewVariableDeclaration = (varDecl) => {
            const varName = varDecl.variableName;
            if (this.variableDeclarations.has(varName)) {
                const varab = this.variableDeclarations.get(varName);
                let prevDeclError = "";
                const debugMetadata = varab.debugMetadata;
                if (debugMetadata) {
                    prevDeclError = ` (${varab.debugMetadata})`;
                }
                this.Error(`found declaration variable '${varName}' that was already declared${prevDeclError}`, varDecl, false);
                return;
            }
            this.variableDeclarations.set(varDecl.variableName, varDecl);
        };
        this.ResolveWeavePointNaming = () => {
            // Find all weave points and organise them by name ready for
            // diverting. Also detect naming collisions.
            if (this._rootWeave) {
                this._rootWeave.ResolveWeavePointNaming();
            }
            for (const [, value] of this._subFlowsByName) {
                if (value.hasOwnProperty("ResolveWeavePointNaming")) {
                    value.ResolveWeavePointNaming();
                }
            }
        };
        this.GenerateRuntimeObject = () => {
            var _a;
            let foundReturn = null;
            if (this.isFunction) {
                this.CheckForDisallowedFunctionFlowControl();
            }
            else if (this.flowLevel === FlowLevel_1.FlowLevel.Knot ||
                this.flowLevel === FlowLevel_1.FlowLevel.Stitch) {
                // Non-functon: Make sure knots and stitches don't attempt to use Return statement
                foundReturn = this.Find(ReturnType_1.ReturnType)();
                if (foundReturn !== null) {
                    this.Error(`Return statements can only be used in knots that are declared as functions: == function ${this.identifier} ==`, foundReturn);
                }
            }
            const container = new Container_1.Container();
            container.name = (_a = this.identifier) === null || _a === void 0 ? void 0 : _a.name;
            if (this.story.countAllVisits) {
                container.visitsShouldBeCounted = true;
            }
            this.GenerateArgumentVariableAssignments(container);
            // Run through content defined for this knot/stitch:
            //  - First of all, any initial content before a sub-stitch
            //    or any weave content is added to the main content container
            //  - The first inner knot/stitch is automatically entered, while
            //    the others are only accessible by an explicit divert
            //       - The exception to this rule is if the knot/stitch takes
            //         parameters, in which case it can't be auto-entered.
            //  - Any Choices and Gathers (i.e. IWeavePoint) found are
            //    processsed by GenerateFlowContent.
            let contentIdx = 0;
            while (this.content !== null && contentIdx < this.content.length) {
                const obj = this.content[contentIdx];
                // Inner knots and stitches
                if (obj instanceof FlowBase) {
                    const childFlow = obj;
                    const childFlowRuntime = childFlow.runtimeObject;
                    // First inner stitch - automatically step into it
                    // 20/09/2016 - let's not auto step into knots
                    if (contentIdx === 0 &&
                        !childFlow.hasParameters &&
                        this.flowLevel === FlowLevel_1.FlowLevel.Knot) {
                        this._startingSubFlowDivert = new Divert_2.Divert();
                        container.AddContent(this._startingSubFlowDivert);
                        this._startingSubFlowRuntime = childFlowRuntime;
                    }
                    // Check for duplicate knots/stitches with same name
                    const namedChild = childFlowRuntime;
                    const existingChild = container.namedContent.get(namedChild.name) || null;
                    if (existingChild) {
                        const errorMsg = `${this.GetType()} already contains flow named '${namedChild.name}' (at ${existingChild.debugMetadata})`;
                        this.Error(errorMsg, childFlow);
                    }
                    container.AddToNamedContentOnly(namedChild);
                }
                else if (obj) {
                    // Other content (including entire Weaves that were grouped in the constructor)
                    // At the time of writing, all FlowBases have a maximum of one piece of "other content"
                    // and it's always the root Weave
                    container.AddContent(obj.runtimeObject);
                }
                contentIdx += 1;
            }
            // CHECK FOR FINAL LOOSE ENDS!
            // Notes:
            //  - Functions don't need to terminate - they just implicitly return
            //  - If return statement was found, don't continue finding warnings for missing control flow,
            // since it's likely that a return statement has been used instead of a ->-> or something,
            // or the writer failed to mark the knot as a function.
            //  - _rootWeave may be null if it's a knot that only has stitches
            if (this.flowLevel !== FlowLevel_1.FlowLevel.Story &&
                !this.isFunction &&
                this._rootWeave !== null &&
                foundReturn === null) {
                this._rootWeave.ValidateTermination(this.WarningInTermination);
            }
            return container;
        };
        this.GenerateArgumentVariableAssignments = (container) => {
            var _a;
            if (this.args === null || this.args.length === 0) {
                return;
            }
            // Assign parameters in reverse since they'll be popped off the evaluation stack
            // No need to generate EvalStart and EvalEnd since there's nothing being pushed
            // back onto the evaluation stack.
            for (let ii = this.args.length - 1; ii >= 0; --ii) {
                const paramName = ((_a = this.args[ii].identifier) === null || _a === void 0 ? void 0 : _a.name) || null;
                const assign = new VariableAssignment_1.VariableAssignment(paramName, true);
                container.AddContent(assign);
            }
        };
        this.ContentWithNameAtLevel = (name, level = null, deepSearch = false) => {
            var _a;
            // Referencing self?
            if (level === this.flowLevel || level === null) {
                if (name === ((_a = this.identifier) === null || _a === void 0 ? void 0 : _a.name)) {
                    return this;
                }
            }
            if (level === FlowLevel_1.FlowLevel.WeavePoint || level === null) {
                let weavePointResult = null;
                if (this._rootWeave) {
                    weavePointResult = this._rootWeave.WeavePointNamed(name);
                    if (weavePointResult) {
                        return weavePointResult;
                    }
                }
                // Stop now if we only wanted a result if it's a weave point?
                if (level === FlowLevel_1.FlowLevel.WeavePoint) {
                    return deepSearch ? this.DeepSearchForAnyLevelContent(name) : null;
                }
            }
            // If this flow would be incapable of containing the requested level, early out
            // (e.g. asking for a Knot from a Stitch)
            if (level !== null && level < this.flowLevel) {
                return null;
            }
            let subFlow = this._subFlowsByName.get(name) || null;
            if (subFlow && (level === null || level === subFlow.flowLevel)) {
                return subFlow;
            }
            return deepSearch ? this.DeepSearchForAnyLevelContent(name) : null;
        };
        this.DeepSearchForAnyLevelContent = (name) => {
            const weaveResultSelf = this.ContentWithNameAtLevel(name, FlowLevel_1.FlowLevel.WeavePoint, false);
            if (weaveResultSelf) {
                return weaveResultSelf;
            }
            for (const [, value] of this._subFlowsByName) {
                const deepResult = value.ContentWithNameAtLevel(name, null, true);
                if (deepResult) {
                    return deepResult;
                }
            }
            return null;
        };
        this.CheckForDisallowedFunctionFlowControl = () => {
            // if (!(this instanceof Knot)) { // cannont use Knot here because of circular dependancy
            if (this.flowLevel !== FlowLevel_1.FlowLevel.Knot) {
                this.Error("Functions cannot be stitches - i.e. they should be defined as '== function myFunc ==' rather than internal to another knot.");
            }
            // Not allowed sub-flows
            for (const [key, value] of this._subFlowsByName) {
                this.Error(`Functions may not contain stitches, but saw '${key}' within the function '${this.identifier}'`, value);
            }
            if (!this._rootWeave) {
                throw new Error();
            }
            const allDiverts = this._rootWeave.FindAll(Divert_1.Divert)();
            for (const divert of allDiverts) {
                if (!divert.isFunctionCall && !(divert.parent instanceof DivertTarget_1.DivertTarget)) {
                    this.Error(`Functions may not contain diverts, but saw '${divert}'`, divert);
                }
            }
            const allChoices = this._rootWeave.FindAll(Choice_1.Choice)();
            for (const choice of allChoices) {
                this.Error(`Functions may not contain choices, but saw '${choice}'`, choice);
            }
        };
        this.WarningInTermination = (terminatingObject) => {
            let message = "Apparent loose end exists where the flow runs out. Do you need a '-> DONE' statement, choice or divert?";
            if (terminatingObject.parent === this._rootWeave && this._firstChildFlow) {
                message = `${message} Note that if you intend to enter '${this._firstChildFlow.identifier}' next, you need to divert to it explicitly.`;
            }
            const terminatingDivert = (0, TypeAssertion_1.asOrNull)(terminatingObject, Divert_1.Divert);
            if (terminatingDivert && terminatingDivert.isTunnel) {
                message += ` When final tunnel to '${terminatingDivert.target} ->' returns it won't have anywhere to go.`;
            }
            this.Warning(message, terminatingObject);
        };
        this.toString = () => `${this.typeName} '${this.identifier}'`;
        this.identifier = identifier;
        this.args = args;
        if (topLevelObjects === null) {
            topLevelObjects = [];
        }
        // Used by story to add includes
        this.PreProcessTopLevelObjects(topLevelObjects);
        topLevelObjects = this.SplitWeaveAndSubFlowContent(topLevelObjects, this.GetType() == "Story" && !isIncludedStory);
        this.AddContent(topLevelObjects);
    }
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    PreProcessTopLevelObjects(_) {
        // empty by default, used by Story to process included file references
    }
    ResolveReferences(context) {
        var _a, _b;
        if (this._startingSubFlowDivert) {
            if (!this._startingSubFlowRuntime) {
                throw new Error();
            }
            this._startingSubFlowDivert.targetPath =
                this._startingSubFlowRuntime.path;
        }
        super.ResolveReferences(context);
        // Check validity of parameter names
        if (this.args !== null) {
            for (const arg of this.args) {
                context.CheckForNamingCollisions(this, arg.identifier, SymbolType_1.SymbolType.Arg, "argument");
            }
            // Separately, check for duplicate arugment names, since they aren't Parsed.Objects,
            // so have to be checked independently.
            for (let ii = 0; ii < this.args.length; ii += 1) {
                for (let jj = ii + 1; jj < this.args.length; jj += 1) {
                    if (((_a = this.args[ii].identifier) === null || _a === void 0 ? void 0 : _a.name) == ((_b = this.args[jj].identifier) === null || _b === void 0 ? void 0 : _b.name)) {
                        this.Error(`Multiple arguments with the same name: '${this.args[ii].identifier}'`);
                    }
                }
            }
        }
        // Check naming collisions for knots and stitches
        if (this.flowLevel !== FlowLevel_1.FlowLevel.Story) {
            // Weave points aren't FlowBases, so this will only be knot or stitch
            const symbolType = this.flowLevel === FlowLevel_1.FlowLevel.Knot
                ? SymbolType_1.SymbolType.Knot
                : SymbolType_1.SymbolType.SubFlowAndWeave;
            context.CheckForNamingCollisions(this, this.identifier, symbolType);
        }
    }
}
exports.FlowBase = FlowBase;
//# sourceMappingURL=FlowBase.js.map