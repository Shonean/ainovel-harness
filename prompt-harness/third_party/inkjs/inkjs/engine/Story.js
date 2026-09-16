"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.Story = exports.InkList = void 0;
const Container_1 = require("./Container");
const Object_1 = require("./Object");
const JsonSerialisation_1 = require("./JsonSerialisation");
const StoryState_1 = require("./StoryState");
const ControlCommand_1 = require("./ControlCommand");
const PushPop_1 = require("./PushPop");
const ChoicePoint_1 = require("./ChoicePoint");
const Choice_1 = require("./Choice");
const Divert_1 = require("./Divert");
const Value_1 = require("./Value");
const Path_1 = require("./Path");
const Void_1 = require("./Void");
const Tag_1 = require("./Tag");
const VariableAssignment_1 = require("./VariableAssignment");
const VariableReference_1 = require("./VariableReference");
const NativeFunctionCall_1 = require("./NativeFunctionCall");
const StoryException_1 = require("./StoryException");
const PRNG_1 = require("./PRNG");
const StringBuilder_1 = require("./StringBuilder");
const ListDefinitionsOrigin_1 = require("./ListDefinitionsOrigin");
const StopWatch_1 = require("./StopWatch");
const Pointer_1 = require("./Pointer");
const InkList_1 = require("./InkList");
const TypeAssertion_1 = require("./TypeAssertion");
const NullException_1 = require("./NullException");
const SimpleJson_1 = require("./SimpleJson");
const Error_1 = require("./Error");
var InkList_2 = require("./InkList");
Object.defineProperty(exports, "InkList", { enumerable: true, get: function () { return InkList_2.InkList; } });
if (!Number.isInteger) {
    Number.isInteger = function isInteger(nVal) {
        return (typeof nVal === "number" &&
            isFinite(nVal) &&
            nVal > -9007199254740992 &&
            nVal < 9007199254740992 &&
            Math.floor(nVal) === nVal);
    };
}
class Story extends Object_1.InkObject {
    get currentChoices() {
        let choices = [];
        if (this._state === null) {
            return (0, NullException_1.throwNullException)("this._state");
        }
        for (let c of this._state.currentChoices) {
            if (!c.isInvisibleDefault) {
                c.index = choices.length;
                choices.push(c);
            }
        }
        return choices;
    }
    get currentText() {
        this.IfAsyncWeCant("call currentText since it's a work in progress");
        return this.state.currentText;
    }
    get currentTags() {
        this.IfAsyncWeCant("call currentTags since it's a work in progress");
        return this.state.currentTags;
    }
    get currentErrors() {
        return this.state.currentErrors;
    }
    get currentWarnings() {
        return this.state.currentWarnings;
    }
    get currentFlowName() {
        return this.state.currentFlowName;
    }
    get currentFlowIsDefaultFlow() {
        return this.state.currentFlowIsDefaultFlow;
    }
    get aliveFlowNames() {
        return this.state.aliveFlowNames;
    }
    get hasError() {
        return this.state.hasError;
    }
    get hasWarning() {
        return this.state.hasWarning;
    }
    get variablesState() {
        return this.state.variablesState;
    }
    get listDefinitions() {
        return this._listDefinitions;
    }
    get state() {
        return this._state;
    }
    // TODO: Implement Profiler
    StartProfiling() {
        /* */
    }
    EndProfiling() {
        /* */
    }
    constructor() {
        super();
        this.inkVersionMinimumCompatible = 18;
        this.onError = null;
        this.onDidContinue = null;
        this.onMakeChoice = null;
        this.onEvaluateFunction = null;
        this.onCompleteEvaluateFunction = null;
        this.onChoosePathString = null;
        this._prevContainers = [];
        this.allowExternalFunctionFallbacks = false;
        this._listDefinitions = null;
        this._variableObservers = null;
        this._hasValidatedExternals = false;
        this._temporaryEvaluationContainer = null;
        this._asyncContinueActive = false;
        this._stateSnapshotAtLastNewline = null;
        this._sawLookaheadUnsafeFunctionAfterNewline = false;
        this._recursiveContinueCount = 0;
        this._asyncSaving = false;
        this._profiler = null; // TODO: Profiler
        // Discrimination between constructors
        let contentContainer;
        let lists = null;
        let json = null;
        if (arguments[0] instanceof Container_1.Container) {
            contentContainer = arguments[0];
            if (typeof arguments[1] !== "undefined") {
                lists = arguments[1];
            }
            // ------ Story (Container contentContainer, List<Runtime.ListDefinition> lists = null)
            this._mainContentContainer = contentContainer;
            // ------
        }
        else {
            if (typeof arguments[0] === "string") {
                let jsonString = arguments[0];
                json = SimpleJson_1.SimpleJson.TextToDictionary(jsonString);
            }
            else {
                json = arguments[0];
            }
        }
        // ------ Story (Container contentContainer, List<Runtime.ListDefinition> lists = null)
        if (lists != null)
            this._listDefinitions = new ListDefinitionsOrigin_1.ListDefinitionsOrigin(lists);
        this._externals = new Map();
        // ------
        // ------ Story(string jsonString) : this((Container)null)
        if (json !== null) {
            let rootObject = json;
            let versionObj = rootObject["inkVersion"];
            if (versionObj == null)
                throw new Error("ink version number not found. Are you sure it's a valid .ink.json file?");
            let formatFromFile = parseInt(versionObj);
            if (formatFromFile > Story.inkVersionCurrent) {
                throw new Error("Version of ink used to build story was newer than the current version of the engine");
            }
            else if (formatFromFile < this.inkVersionMinimumCompatible) {
                throw new Error("Version of ink used to build story is too old to be loaded by this version of the engine");
            }
            else if (formatFromFile != Story.inkVersionCurrent) {
                console.warn(`WARNING: Version of ink ${Story.inkVersionCurrent} used to build story doesn't match current version of engine (${formatFromFile}). Non-critical, but recommend synchronising.`);
            }
            let rootToken = rootObject["root"];
            if (rootToken == null)
                throw new Error("Root node for ink not found. Are you sure it's a valid .ink.json file?");
            let listDefsObj;
            if ((listDefsObj = rootObject["listDefs"])) {
                this._listDefinitions =
                    JsonSerialisation_1.JsonSerialisation.JTokenToListDefinitions(listDefsObj);
            }
            this._mainContentContainer = (0, TypeAssertion_1.asOrThrows)(JsonSerialisation_1.JsonSerialisation.JTokenToRuntimeObject(rootToken), Container_1.Container);
            this.ResetState();
        }
        // ------
    }
    // Merge together `public string ToJson()` and `void ToJson(SimpleJson.Writer writer)`.
    // Will only return a value if writer was not provided.
    ToJson(writer) {
        let shouldReturn = false;
        if (!writer) {
            shouldReturn = true;
            writer = new SimpleJson_1.SimpleJson.Writer();
        }
        writer.WriteObjectStart();
        writer.WriteIntProperty("inkVersion", Story.inkVersionCurrent);
        writer.WriteProperty("root", (w) => JsonSerialisation_1.JsonSerialisation.WriteRuntimeContainer(w, this._mainContentContainer));
        if (this._listDefinitions != null) {
            writer.WritePropertyStart("listDefs");
            writer.WriteObjectStart();
            for (let def of this._listDefinitions.lists) {
                writer.WritePropertyStart(def.name);
                writer.WriteObjectStart();
                for (let [key, value] of def.items) {
                    let item = InkList_1.InkListItem.fromSerializedKey(key);
                    let val = value;
                    writer.WriteIntProperty(item.itemName, val);
                }
                writer.WriteObjectEnd();
                writer.WritePropertyEnd();
            }
            writer.WriteObjectEnd();
            writer.WritePropertyEnd();
        }
        writer.WriteObjectEnd();
        if (shouldReturn)
            return writer.toString();
    }
    ResetState() {
        this.IfAsyncWeCant("ResetState");
        this._state = new StoryState_1.StoryState(this);
        this._state.variablesState.ObserveVariableChange(this.VariableStateDidChangeEvent.bind(this));
        this.ResetGlobals();
    }
    ResetErrors() {
        if (this._state === null) {
            return (0, NullException_1.throwNullException)("this._state");
        }
        this._state.ResetErrors();
    }
    ResetCallstack() {
        this.IfAsyncWeCant("ResetCallstack");
        if (this._state === null) {
            return (0, NullException_1.throwNullException)("this._state");
        }
        this._state.ForceEnd();
    }
    ResetGlobals() {
        if (this._mainContentContainer.namedContent.get("global decl")) {
            let originalPointer = this.state.currentPointer.copy();
            this.ChoosePath(new Path_1.Path("global decl"), false);
            this.ContinueInternal();
            this.state.currentPointer = originalPointer;
        }
        this.state.variablesState.SnapshotDefaultGlobals();
    }
    SwitchFlow(flowName) {
        this.IfAsyncWeCant("switch flow");
        if (this._asyncSaving) {
            throw new Error("Story is already in background saving mode, can't switch flow to " +
                flowName);
        }
        this.state.SwitchFlow_Internal(flowName);
    }
    RemoveFlow(flowName) {
        this.state.RemoveFlow_Internal(flowName);
    }
    SwitchToDefaultFlow() {
        this.state.SwitchToDefaultFlow_Internal();
    }
    Continue() {
        this.ContinueAsync(0);
        return this.currentText;
    }
    get canContinue() {
        return this.state.canContinue;
    }
    get asyncContinueComplete() {
        return !this._asyncContinueActive;
    }
    ContinueAsync(millisecsLimitAsync) {
        if (!this._hasValidatedExternals)
            this.ValidateExternalBindings();
        this.ContinueInternal(millisecsLimitAsync);
    }
    ContinueInternal(millisecsLimitAsync = 0) {
        if (this._profiler != null)
            this._profiler.PreContinue();
        let isAsyncTimeLimited = millisecsLimitAsync > 0;
        this._recursiveContinueCount++;
        if (!this._asyncContinueActive) {
            this._asyncContinueActive = isAsyncTimeLimited;
            if (!this.canContinue) {
                throw new Error("Can't continue - should check canContinue before calling Continue");
            }
            this._state.didSafeExit = false;
            this._state.ResetOutput();
            if (this._recursiveContinueCount == 1)
                this._state.variablesState.StartVariableObservation();
        }
        else if (this._asyncContinueActive && !isAsyncTimeLimited) {
            this._asyncContinueActive = false;
        }
        let durationStopwatch = new StopWatch_1.Stopwatch();
        durationStopwatch.Start();
        let outputStreamEndsInNewline = false;
        this._sawLookaheadUnsafeFunctionAfterNewline = false;
        do {
            try {
                outputStreamEndsInNewline = this.ContinueSingleStep();
            }
            catch (e) {
                if (!(e instanceof StoryException_1.StoryException))
                    throw e;
                this.AddError(e.message, undefined, e.useEndLineNumber);
                break;
            }
            if (outputStreamEndsInNewline)
                break;
            if (this._asyncContinueActive &&
                durationStopwatch.ElapsedMilliseconds > millisecsLimitAsync) {
                break;
            }
        } while (this.canContinue);
        durationStopwatch.Stop();
        let changedVariablesToObserve = null;
        if (outputStreamEndsInNewline || !this.canContinue) {
            if (this._stateSnapshotAtLastNewline !== null) {
                this.RestoreStateSnapshot();
            }
            if (!this.canContinue) {
                if (this.state.callStack.canPopThread)
                    this.AddError("Thread available to pop, threads should always be flat by the end of evaluation?");
                if (this.state.generatedChoices.length == 0 &&
                    !this.state.didSafeExit &&
                    this._temporaryEvaluationContainer == null) {
                    if (this.state.callStack.CanPop(PushPop_1.PushPopType.Tunnel))
                        this.AddError("unexpectedly reached end of content. Do you need a '->->' to return from a tunnel?");
                    else if (this.state.callStack.CanPop(PushPop_1.PushPopType.Function))
                        this.AddError("unexpectedly reached end of content. Do you need a '~ return'?");
                    else if (!this.state.callStack.canPop)
                        this.AddError("ran out of content. Do you need a '-> DONE' or '-> END'?");
                    else
                        this.AddError("unexpectedly reached end of content for unknown reason. Please debug compiler!");
                }
            }
            this.state.didSafeExit = false;
            this._sawLookaheadUnsafeFunctionAfterNewline = false;
            if (this._recursiveContinueCount == 1)
                changedVariablesToObserve =
                    this._state.variablesState.CompleteVariableObservation();
            this._asyncContinueActive = false;
            if (this.onDidContinue !== null)
                this.onDidContinue();
        }
        this._recursiveContinueCount--;
        if (this._profiler != null)
            this._profiler.PostContinue();
        // In the following code, we're masking a lot of non-null assertion,
        // because testing for against `hasError` or `hasWarning` makes sure
        // the arrays are present and contain at least one element.
        if (this.state.hasError || this.state.hasWarning) {
            if (this.onError !== null) {
                if (this.state.hasError) {
                    for (let err of this.state.currentErrors) {
                        this.onError(err, Error_1.ErrorType.Error);
                    }
                }
                if (this.state.hasWarning) {
                    for (let err of this.state.currentWarnings) {
                        this.onError(err, Error_1.ErrorType.Warning);
                    }
                }
                this.ResetErrors();
            }
            else {
                let sb = new StringBuilder_1.StringBuilder();
                sb.Append("Ink had ");
                if (this.state.hasError) {
                    sb.Append(`${this.state.currentErrors.length}`);
                    sb.Append(this.state.currentErrors.length == 1 ? " error" : " errors");
                    if (this.state.hasWarning)
                        sb.Append(" and ");
                }
                if (this.state.hasWarning) {
                    sb.Append(`${this.state.currentWarnings.length}`);
                    sb.Append(this.state.currentWarnings.length == 1 ? " warning" : " warnings");
                    if (this.state.hasWarning)
                        sb.Append(" and ");
                }
                sb.Append(". It is strongly suggested that you assign an error handler to story.onError. The first issue was: ");
                sb.Append(this.state.hasError
                    ? this.state.currentErrors[0]
                    : this.state.currentWarnings[0]);
                throw new StoryException_1.StoryException(sb.toString());
            }
        }
        if (changedVariablesToObserve != null &&
            Object.keys(changedVariablesToObserve).length > 0) {
            this._state.variablesState.NotifyObservers(changedVariablesToObserve);
        }
    }
    ContinueSingleStep() {
        if (this._profiler != null)
            this._profiler.PreStep();
        this.Step();
        if (this._profiler != null)
            this._profiler.PostStep();
        if (!this.canContinue && !this.state.callStack.elementIsEvaluateFromGame) {
            this.TryFollowDefaultInvisibleChoice();
        }
        if (this._profiler != null)
            this._profiler.PreSnapshot();
        if (!this.state.inStringEvaluation) {
            if (this._stateSnapshotAtLastNewline !== null) {
                if (this._stateSnapshotAtLastNewline.currentTags === null) {
                    return (0, NullException_1.throwNullException)("this._stateAtLastNewline.currentTags");
                }
                if (this.state.currentTags === null) {
                    return (0, NullException_1.throwNullException)("this.state.currentTags");
                }
                let change = this.CalculateNewlineOutputStateChange(this._stateSnapshotAtLastNewline.currentText, this.state.currentText, this._stateSnapshotAtLastNewline.currentTags.length, this.state.currentTags.length);
                if (change == Story.OutputStateChange.ExtendedBeyondNewline ||
                    this._sawLookaheadUnsafeFunctionAfterNewline) {
                    this.RestoreStateSnapshot();
                    return true;
                }
                else if (change == Story.OutputStateChange.NewlineRemoved) {
                    this.DiscardSnapshot();
                }
            }
            if (this.state.outputStreamEndsInNewline) {
                if (this.canContinue) {
                    if (this._stateSnapshotAtLastNewline == null)
                        this.StateSnapshot();
                }
                else {
                    this.DiscardSnapshot();
                }
            }
        }
        if (this._profiler != null)
            this._profiler.PostSnapshot();
        return false;
    }
    CalculateNewlineOutputStateChange(prevText, currText, prevTagCount, currTagCount) {
        if (prevText === null) {
            return (0, NullException_1.throwNullException)("prevText");
        }
        if (currText === null) {
            return (0, NullException_1.throwNullException)("currText");
        }
        let newlineStillExists = currText.length >= prevText.length &&
            prevText.length > 0 &&
            currText.charAt(prevText.length - 1) == "\n";
        if (prevTagCount == currTagCount &&
            prevText.length == currText.length &&
            newlineStillExists)
            return Story.OutputStateChange.NoChange;
        if (!newlineStillExists) {
            return Story.OutputStateChange.NewlineRemoved;
        }
        if (currTagCount > prevTagCount)
            return Story.OutputStateChange.ExtendedBeyondNewline;
        for (let i = prevText.length; i < currText.length; i++) {
            let c = currText.charAt(i);
            if (c != " " && c != "\t") {
                return Story.OutputStateChange.ExtendedBeyondNewline;
            }
        }
        return Story.OutputStateChange.NoChange;
    }
    ContinueMaximally() {
        this.IfAsyncWeCant("ContinueMaximally");
        let sb = new StringBuilder_1.StringBuilder();
        while (this.canContinue) {
            sb.Append(this.Continue());
        }
        return sb.toString();
    }
    ContentAtPath(path) {
        return this.mainContentContainer.ContentAtPath(path);
    }
    KnotContainerWithName(name) {
        let namedContainer = this.mainContentContainer.namedContent.get(name);
        if (namedContainer instanceof Container_1.Container)
            return namedContainer;
        else
            return null;
    }
    PointerAtPath(path) {
        if (path.length == 0)
            return Pointer_1.Pointer.Null;
        let p = new Pointer_1.Pointer();
        let pathLengthToUse = path.length;
        let result = null;
        if (path.lastComponent === null) {
            return (0, NullException_1.throwNullException)("path.lastComponent");
        }
        if (path.lastComponent.isIndex) {
            pathLengthToUse = path.length - 1;
            result = this.mainContentContainer.ContentAtPath(path, undefined, pathLengthToUse);
            p.container = result.container;
            p.index = path.lastComponent.index;
        }
        else {
            result = this.mainContentContainer.ContentAtPath(path);
            p.container = result.container;
            p.index = -1;
        }
        if (result.obj == null ||
            (result.obj == this.mainContentContainer && pathLengthToUse > 0)) {
            this.Error("Failed to find content at path '" +
                path +
                "', and no approximation of it was possible.");
        }
        else if (result.approximate)
            this.Warning("Failed to find content at path '" +
                path +
                "', so it was approximated to: '" +
                result.obj.path +
                "'.");
        return p;
    }
    StateSnapshot() {
        this._stateSnapshotAtLastNewline = this._state;
        this._state = this._state.CopyAndStartPatching(false);
    }
    RestoreStateSnapshot() {
        if (this._stateSnapshotAtLastNewline === null) {
            (0, NullException_1.throwNullException)("_stateSnapshotAtLastNewline");
        }
        this._stateSnapshotAtLastNewline.RestoreAfterPatch();
        this._state = this._stateSnapshotAtLastNewline;
        this._stateSnapshotAtLastNewline = null;
        if (!this._asyncSaving) {
            this._state.ApplyAnyPatch();
        }
    }
    DiscardSnapshot() {
        if (!this._asyncSaving)
            this._state.ApplyAnyPatch();
        this._stateSnapshotAtLastNewline = null;
    }
    CopyStateForBackgroundThreadSave() {
        this.IfAsyncWeCant("start saving on a background thread");
        if (this._asyncSaving)
            throw new Error("Story is already in background saving mode, can't call CopyStateForBackgroundThreadSave again!");
        let stateToSave = this._state;
        this._state = this._state.CopyAndStartPatching(true);
        this._asyncSaving = true;
        return stateToSave;
    }
    BackgroundSaveComplete() {
        if (this._stateSnapshotAtLastNewline === null) {
            this._state.ApplyAnyPatch();
        }
        this._asyncSaving = false;
    }
    Step() {
        let shouldAddToStream = true;
        let pointer = this.state.currentPointer.copy();
        if (pointer.isNull) {
            return;
        }
        // Container containerToEnter = pointer.Resolve () as Container;
        let containerToEnter = (0, TypeAssertion_1.asOrNull)(pointer.Resolve(), Container_1.Container);
        while (containerToEnter) {
            this.VisitContainer(containerToEnter, true);
            // No content? the most we can do is step past it
            if (containerToEnter.content.length == 0) {
                break;
            }
            pointer = Pointer_1.Pointer.StartOf(containerToEnter);
            // containerToEnter = pointer.Resolve() as Container;
            containerToEnter = (0, TypeAssertion_1.asOrNull)(pointer.Resolve(), Container_1.Container);
        }
        this.state.currentPointer = pointer.copy();
        if (this._profiler != null)
            this._profiler.Step(this.state.callStack);
        // Is the current content object:
        //  - Normal content
        //  - Or a logic/flow statement - if so, do it
        // Stop flow if we hit a stack pop when we're unable to pop (e.g. return/done statement in knot
        // that was diverted to rather than called as a function)
        let currentContentObj = pointer.Resolve();
        let isLogicOrFlowControl = this.PerformLogicAndFlowControl(currentContentObj);
        // Has flow been forced to end by flow control above?
        if (this.state.currentPointer.isNull) {
            return;
        }
        if (isLogicOrFlowControl) {
            shouldAddToStream = false;
        }
        // Choice with condition?
        // var choicePoint = currentContentObj as ChoicePoint;
        let choicePoint = (0, TypeAssertion_1.asOrNull)(currentContentObj, ChoicePoint_1.ChoicePoint);
        if (choicePoint) {
            let choice = this.ProcessChoice(choicePoint);
            if (choice) {
                this.state.generatedChoices.push(choice);
            }
            currentContentObj = null;
            shouldAddToStream = false;
        }
        // If the container has no content, then it will be
        // the "content" itself, but we skip over it.
        if (currentContentObj instanceof Container_1.Container) {
            shouldAddToStream = false;
        }
        // Content to add to evaluation stack or the output stream
        if (shouldAddToStream) {
            // If we're pushing a variable pointer onto the evaluation stack, ensure that it's specific
            // to our current (possibly temporary) context index. And make a copy of the pointer
            // so that we're not editing the original runtime object.
            // var varPointer = currentContentObj as VariablePointerValue;
            let varPointer = (0, TypeAssertion_1.asOrNull)(currentContentObj, Value_1.VariablePointerValue);
            if (varPointer && varPointer.contextIndex == -1) {
                // Create new object so we're not overwriting the story's own data
                let contextIdx = this.state.callStack.ContextForVariableNamed(varPointer.variableName);
                currentContentObj = new Value_1.VariablePointerValue(varPointer.variableName, contextIdx);
            }
            // Expression evaluation content
            if (this.state.inExpressionEvaluation) {
                this.state.PushEvaluationStack(currentContentObj);
            }
            // Output stream content (i.e. not expression evaluation)
            else {
                this.state.PushToOutputStream(currentContentObj);
            }
        }
        // Increment the content pointer, following diverts if necessary
        this.NextContent();
        // Starting a thread should be done after the increment to the content pointer,
        // so that when returning from the thread, it returns to the content after this instruction.
        // var controlCmd = currentContentObj as ;
        let controlCmd = (0, TypeAssertion_1.asOrNull)(currentContentObj, ControlCommand_1.ControlCommand);
        if (controlCmd &&
            controlCmd.commandType == ControlCommand_1.ControlCommand.CommandType.StartThread) {
            this.state.callStack.PushThread();
        }
    }
    VisitContainer(container, atStart) {
        if (!container.countingAtStartOnly || atStart) {
            if (container.visitsShouldBeCounted)
                this.state.IncrementVisitCountForContainer(container);
            if (container.turnIndexShouldBeCounted)
                this.state.RecordTurnIndexVisitToContainer(container);
        }
    }
    VisitChangedContainersDueToDivert() {
        let previousPointer = this.state.previousPointer.copy();
        let pointer = this.state.currentPointer.copy();
        if (pointer.isNull || pointer.index == -1)
            return;
        this._prevContainers.length = 0;
        if (!previousPointer.isNull) {
            // Container prevAncestor = previousPointer.Resolve() as Container ?? previousPointer.container as Container;
            let resolvedPreviousAncestor = previousPointer.Resolve();
            let prevAncestor = (0, TypeAssertion_1.asOrNull)(resolvedPreviousAncestor, Container_1.Container) ||
                (0, TypeAssertion_1.asOrNull)(previousPointer.container, Container_1.Container);
            while (prevAncestor) {
                this._prevContainers.push(prevAncestor);
                // prevAncestor = prevAncestor.parent as Container;
                prevAncestor = (0, TypeAssertion_1.asOrNull)(prevAncestor.parent, Container_1.Container);
            }
        }
        let currentChildOfContainer = pointer.Resolve();
        if (currentChildOfContainer == null)
            return;
        // Container currentContainerAncestor = currentChildOfContainer.parent as Container;
        let currentContainerAncestor = (0, TypeAssertion_1.asOrNull)(currentChildOfContainer.parent, Container_1.Container);
        let allChildrenEnteredAtStart = true;
        while (currentContainerAncestor &&
            (this._prevContainers.indexOf(currentContainerAncestor) < 0 ||
                currentContainerAncestor.countingAtStartOnly)) {
            // Check whether this ancestor container is being entered at the start,
            // by checking whether the child object is the first.
            let enteringAtStart = currentContainerAncestor.content.length > 0 &&
                currentChildOfContainer == currentContainerAncestor.content[0] &&
                allChildrenEnteredAtStart;
            if (!enteringAtStart)
                allChildrenEnteredAtStart = false;
            // Mark a visit to this container
            this.VisitContainer(currentContainerAncestor, enteringAtStart);
            currentChildOfContainer = currentContainerAncestor;
            // currentContainerAncestor = currentContainerAncestor.parent as Container;
            currentContainerAncestor = (0, TypeAssertion_1.asOrNull)(currentContainerAncestor.parent, Container_1.Container);
        }
    }
    PopChoiceStringAndTags(tags) {
        let choiceOnlyStrVal = (0, TypeAssertion_1.asOrThrows)(this.state.PopEvaluationStack(), Value_1.StringValue);
        while (this.state.evaluationStack.length > 0 &&
            (0, TypeAssertion_1.asOrNull)(this.state.PeekEvaluationStack(), Tag_1.Tag) != null) {
            let tag = (0, TypeAssertion_1.asOrNull)(this.state.PopEvaluationStack(), Tag_1.Tag);
            if (tag)
                tags.push(tag.text);
        }
        return choiceOnlyStrVal.value;
    }
    ProcessChoice(choicePoint) {
        let showChoice = true;
        // Don't create choice if choice point doesn't pass conditional
        if (choicePoint.hasCondition) {
            let conditionValue = this.state.PopEvaluationStack();
            if (!this.IsTruthy(conditionValue)) {
                showChoice = false;
            }
        }
        let startText = "";
        let choiceOnlyText = "";
        let tags = [];
        if (choicePoint.hasChoiceOnlyContent) {
            choiceOnlyText = this.PopChoiceStringAndTags(tags) || "";
        }
        if (choicePoint.hasStartContent) {
            startText = this.PopChoiceStringAndTags(tags) || "";
        }
        // Don't create choice if player has already read this content
        if (choicePoint.onceOnly) {
            let visitCount = this.state.VisitCountForContainer(choicePoint.choiceTarget);
            if (visitCount > 0) {
                showChoice = false;
            }
        }
        // We go through the full process of creating the choice above so
        // that we consume the content for it, since otherwise it'll
        // be shown on the output stream.
        if (!showChoice) {
            return null;
        }
        let choice = new Choice_1.Choice();
        choice.targetPath = choicePoint.pathOnChoice;
        choice.sourcePath = choicePoint.path.toString();
        choice.isInvisibleDefault = choicePoint.isInvisibleDefault;
        choice.threadAtGeneration = this.state.callStack.ForkThread();
        choice.tags = tags.reverse(); //C# is a stack
        choice.text = (startText + choiceOnlyText).replace(/^[ \t]+|[ \t]+$/g, "");
        return choice;
    }
    IsTruthy(obj) {
        let truthy = false;
        if (obj instanceof Value_1.Value) {
            let val = obj;
            if (val instanceof Value_1.DivertTargetValue) {
                let divTarget = val;
                this.Error("Shouldn't use a divert target (to " +
                    divTarget.targetPath +
                    ") as a conditional value. Did you intend a function call 'likeThis()' or a read count check 'likeThis'? (no arrows)");
                return false;
            }
            return val.isTruthy;
        }
        return truthy;
    }
    PerformLogicAndFlowControl(contentObj) {
        if (contentObj == null) {
            return false;
        }
        // Divert
        if (contentObj instanceof Divert_1.Divert) {
            let currentDivert = contentObj;
            if (currentDivert.isConditional) {
                let conditionValue = this.state.PopEvaluationStack();
                // False conditional? Cancel divert
                if (!this.IsTruthy(conditionValue))
                    return true;
            }
            if (currentDivert.hasVariableTarget) {
                let varName = currentDivert.variableDivertName;
                let varContents = this.state.variablesState.GetVariableWithName(varName);
                if (varContents == null) {
                    this.Error("Tried to divert using a target from a variable that could not be found (" +
                        varName +
                        ")");
                }
                else if (!(varContents instanceof Value_1.DivertTargetValue)) {
                    // var intContent = varContents as IntValue;
                    let intContent = (0, TypeAssertion_1.asOrNull)(varContents, Value_1.IntValue);
                    let errorMessage = "Tried to divert to a target from a variable, but the variable (" +
                        varName +
                        ") didn't contain a divert target, it ";
                    if (intContent instanceof Value_1.IntValue && intContent.value == 0) {
                        errorMessage += "was empty/null (the value 0).";
                    }
                    else {
                        errorMessage += "contained '" + varContents + "'.";
                    }
                    this.Error(errorMessage);
                }
                let target = (0, TypeAssertion_1.asOrThrows)(varContents, Value_1.DivertTargetValue);
                this.state.divertedPointer = this.PointerAtPath(target.targetPath);
            }
            else if (currentDivert.isExternal) {
                this.CallExternalFunction(currentDivert.targetPathString, currentDivert.externalArgs);
                return true;
            }
            else {
                this.state.divertedPointer = currentDivert.targetPointer.copy();
            }
            if (currentDivert.pushesToStack) {
                this.state.callStack.Push(currentDivert.stackPushType, undefined, this.state.outputStream.length);
            }
            if (this.state.divertedPointer.isNull && !currentDivert.isExternal) {
                if (currentDivert &&
                    currentDivert.debugMetadata &&
                    currentDivert.debugMetadata.sourceName != null) {
                    this.Error("Divert target doesn't exist: " +
                        currentDivert.debugMetadata.sourceName);
                }
                else {
                    this.Error("Divert resolution failed: " + currentDivert);
                }
            }
            return true;
        }
        // Start/end an expression evaluation? Or print out the result?
        else if (contentObj instanceof ControlCommand_1.ControlCommand) {
            let evalCommand = contentObj;
            switch (evalCommand.commandType) {
                case ControlCommand_1.ControlCommand.CommandType.EvalStart:
                    this.Assert(this.state.inExpressionEvaluation === false, "Already in expression evaluation?");
                    this.state.inExpressionEvaluation = true;
                    break;
                case ControlCommand_1.ControlCommand.CommandType.EvalEnd:
                    this.Assert(this.state.inExpressionEvaluation === true, "Not in expression evaluation mode");
                    this.state.inExpressionEvaluation = false;
                    break;
                case ControlCommand_1.ControlCommand.CommandType.EvalOutput:
                    // If the expression turned out to be empty, there may not be anything on the stack
                    if (this.state.evaluationStack.length > 0) {
                        let output = this.state.PopEvaluationStack();
                        // Functions may evaluate to Void, in which case we skip output
                        if (!(output instanceof Void_1.Void)) {
                            // TODO: Should we really always blanket convert to string?
                            // It would be okay to have numbers in the output stream the
                            // only problem is when exporting text for viewing, it skips over numbers etc.
                            let text = new Value_1.StringValue(output.toString());
                            this.state.PushToOutputStream(text);
                        }
                    }
                    break;
                case ControlCommand_1.ControlCommand.CommandType.NoOp:
                    break;
                case ControlCommand_1.ControlCommand.CommandType.Duplicate:
                    this.state.PushEvaluationStack(this.state.PeekEvaluationStack());
                    break;
                case ControlCommand_1.ControlCommand.CommandType.PopEvaluatedValue:
                    this.state.PopEvaluationStack();
                    break;
                case ControlCommand_1.ControlCommand.CommandType.PopFunction:
                case ControlCommand_1.ControlCommand.CommandType.PopTunnel:
                    let popType = evalCommand.commandType == ControlCommand_1.ControlCommand.CommandType.PopFunction
                        ? PushPop_1.PushPopType.Function
                        : PushPop_1.PushPopType.Tunnel;
                    let overrideTunnelReturnTarget = null;
                    if (popType == PushPop_1.PushPopType.Tunnel) {
                        let popped = this.state.PopEvaluationStack();
                        // overrideTunnelReturnTarget = popped as DivertTargetValue;
                        overrideTunnelReturnTarget = (0, TypeAssertion_1.asOrNull)(popped, Value_1.DivertTargetValue);
                        if (overrideTunnelReturnTarget === null) {
                            this.Assert(popped instanceof Void_1.Void, "Expected void if ->-> doesn't override target");
                        }
                    }
                    if (this.state.TryExitFunctionEvaluationFromGame()) {
                        break;
                    }
                    else if (this.state.callStack.currentElement.type != popType ||
                        !this.state.callStack.canPop) {
                        let names = new Map();
                        names.set(PushPop_1.PushPopType.Function, "function return statement (~ return)");
                        names.set(PushPop_1.PushPopType.Tunnel, "tunnel onwards statement (->->)");
                        let expected = names.get(this.state.callStack.currentElement.type);
                        if (!this.state.callStack.canPop) {
                            expected = "end of flow (-> END or choice)";
                        }
                        let errorMsg = "Found " + names.get(popType) + ", when expected " + expected;
                        this.Error(errorMsg);
                    }
                    else {
                        this.state.PopCallStack();
                        if (overrideTunnelReturnTarget)
                            this.state.divertedPointer = this.PointerAtPath(overrideTunnelReturnTarget.targetPath);
                    }
                    break;
                case ControlCommand_1.ControlCommand.CommandType.BeginString:
                    this.state.PushToOutputStream(evalCommand);
                    this.Assert(this.state.inExpressionEvaluation === true, "Expected to be in an expression when evaluating a string");
                    this.state.inExpressionEvaluation = false;
                    break;
                // Leave it to story.currentText and story.currentTags to sort out the text from the tags
                // This is mostly because we can't always rely on the existence of EndTag, and we don't want
                // to try and flatten dynamic tags to strings every time \n is pushed to output
                case ControlCommand_1.ControlCommand.CommandType.BeginTag:
                    this.state.PushToOutputStream(evalCommand);
                    break;
                // EndTag has 2 modes:
                //  - When in string evaluation (for choices)
                //  - Normal
                //
                // The only way you could have an EndTag in the middle of
                // string evaluation is if we're currently generating text for a
                // choice, such as:
                //
                //   + choice # tag
                //
                // In the above case, the ink will be run twice:
                //  - First, to generate the choice text. String evaluation
                //    will be on, and the final string will be pushed to the
                //    evaluation stack, ready to be popped to make a Choice
                //    object.
                //  - Second, when ink generates text after choosing the choice.
                //    On this ocassion, it's not in string evaluation mode.
                //
                // On the writing side, we disallow manually putting tags within
                // strings like this:
                //
                //   {"hello # world"}
                //
                // So we know that the tag must be being generated as part of
                // choice content. Therefore, when the tag has been generated,
                // we push it onto the evaluation stack in the exact same way
                // as the string for the choice content.
                case ControlCommand_1.ControlCommand.CommandType.EndTag: {
                    if (this.state.inStringEvaluation) {
                        let contentStackForTag = [];
                        let outputCountConsumed = 0;
                        for (let i = this.state.outputStream.length - 1; i >= 0; --i) {
                            let obj = this.state.outputStream[i];
                            outputCountConsumed++;
                            // var command = obj as ControlCommand;
                            let command = (0, TypeAssertion_1.asOrNull)(obj, ControlCommand_1.ControlCommand);
                            if (command != null) {
                                if (command.commandType == ControlCommand_1.ControlCommand.CommandType.BeginTag) {
                                    break;
                                }
                                else {
                                    this.Error("Unexpected ControlCommand while extracting tag from choice");
                                    break;
                                }
                            }
                            if (obj instanceof Value_1.StringValue) {
                                contentStackForTag.push(obj);
                            }
                        }
                        // Consume the content that was produced for this string
                        this.state.PopFromOutputStream(outputCountConsumed);
                        // Build string out of the content we collected
                        let sb = new StringBuilder_1.StringBuilder();
                        for (let strVal of contentStackForTag.reverse()) {
                            sb.Append(strVal.toString());
                        }
                        let choiceTag = new Tag_1.Tag(this.state.CleanOutputWhitespace(sb.toString()));
                        // Pushing to the evaluation stack means it gets picked up
                        // when a Choice is generated from the next Choice Point.
                        this.state.PushEvaluationStack(choiceTag);
                    }
                    else {
                        // Otherwise! Simply push EndTag, so that in the output stream we
                        // have a structure of: [BeginTag, "the tag content", EndTag]
                        this.state.PushToOutputStream(evalCommand);
                    }
                    break;
                }
                case ControlCommand_1.ControlCommand.CommandType.EndString: {
                    let contentStackForString = [];
                    let contentToRetain = [];
                    let outputCountConsumed = 0;
                    for (let i = this.state.outputStream.length - 1; i >= 0; --i) {
                        let obj = this.state.outputStream[i];
                        outputCountConsumed++;
                        // var command = obj as ControlCommand;
                        let command = (0, TypeAssertion_1.asOrNull)(obj, ControlCommand_1.ControlCommand);
                        if (command &&
                            command.commandType == ControlCommand_1.ControlCommand.CommandType.BeginString) {
                            break;
                        }
                        if (obj instanceof Tag_1.Tag) {
                            contentToRetain.push(obj);
                        }
                        if (obj instanceof Value_1.StringValue) {
                            contentStackForString.push(obj);
                        }
                    }
                    // Consume the content that was produced for this string
                    this.state.PopFromOutputStream(outputCountConsumed);
                    // Rescue the tags that we want actually to keep on the output stack
                    // rather than consume as part of the string we're building.
                    // At the time of writing, this only applies to Tag objects generated
                    // by choices, which are pushed to the stack during string generation.
                    for (let rescuedTag of contentToRetain)
                        this.state.PushToOutputStream(rescuedTag);
                    // The C# version uses a Stack for contentStackForString, but we're
                    // using a simple array, so we need to reverse it before using it
                    contentStackForString = contentStackForString.reverse();
                    // Build string out of the content we collected
                    let sb = new StringBuilder_1.StringBuilder();
                    for (let c of contentStackForString) {
                        sb.Append(c.toString());
                    }
                    // Return to expression evaluation (from content mode)
                    this.state.inExpressionEvaluation = true;
                    this.state.PushEvaluationStack(new Value_1.StringValue(sb.toString()));
                    break;
                }
                case ControlCommand_1.ControlCommand.CommandType.ChoiceCount:
                    let choiceCount = this.state.generatedChoices.length;
                    this.state.PushEvaluationStack(new Value_1.IntValue(choiceCount));
                    break;
                case ControlCommand_1.ControlCommand.CommandType.Turns:
                    this.state.PushEvaluationStack(new Value_1.IntValue(this.state.currentTurnIndex + 1));
                    break;
                case ControlCommand_1.ControlCommand.CommandType.TurnsSince:
                case ControlCommand_1.ControlCommand.CommandType.ReadCount:
                    let target = this.state.PopEvaluationStack();
                    if (!(target instanceof Value_1.DivertTargetValue)) {
                        let extraNote = "";
                        if (target instanceof Value_1.IntValue)
                            extraNote =
                                ". Did you accidentally pass a read count ('knot_name') instead of a target ('-> knot_name')?";
                        this.Error("TURNS_SINCE / READ_COUNT expected a divert target (knot, stitch, label name), but saw " +
                            target +
                            extraNote);
                        break;
                    }
                    // var divertTarget = target as DivertTargetValue;
                    let divertTarget = (0, TypeAssertion_1.asOrThrows)(target, Value_1.DivertTargetValue);
                    // var container = ContentAtPath (divertTarget.targetPath).correctObj as Container;
                    let container = (0, TypeAssertion_1.asOrNull)(this.ContentAtPath(divertTarget.targetPath).correctObj, Container_1.Container);
                    let eitherCount;
                    if (container != null) {
                        if (evalCommand.commandType == ControlCommand_1.ControlCommand.CommandType.TurnsSince)
                            eitherCount = this.state.TurnsSinceForContainer(container);
                        else
                            eitherCount = this.state.VisitCountForContainer(container);
                    }
                    else {
                        if (evalCommand.commandType == ControlCommand_1.ControlCommand.CommandType.TurnsSince)
                            eitherCount = -1;
                        else
                            eitherCount = 0;
                        this.Warning("Failed to find container for " +
                            evalCommand.toString() +
                            " lookup at " +
                            divertTarget.targetPath.toString());
                    }
                    this.state.PushEvaluationStack(new Value_1.IntValue(eitherCount));
                    break;
                case ControlCommand_1.ControlCommand.CommandType.Random: {
                    let maxInt = (0, TypeAssertion_1.asOrNull)(this.state.PopEvaluationStack(), Value_1.IntValue);
                    let minInt = (0, TypeAssertion_1.asOrNull)(this.state.PopEvaluationStack(), Value_1.IntValue);
                    if (minInt == null || minInt instanceof Value_1.IntValue === false)
                        return this.Error("Invalid value for minimum parameter of RANDOM(min, max)");
                    if (maxInt == null || maxInt instanceof Value_1.IntValue === false)
                        return this.Error("Invalid value for maximum parameter of RANDOM(min, max)");
                    // Originally a primitive type, but here, can be null.
                    // TODO: Replace by default value?
                    if (maxInt.value === null) {
                        return (0, NullException_1.throwNullException)("maxInt.value");
                    }
                    if (minInt.value === null) {
                        return (0, NullException_1.throwNullException)("minInt.value");
                    }
                    // This code is differs a bit from the reference implementation, since
                    // JavaScript has no true integers. Hence integer arithmetics and
                    // interger overflows don't apply here. A loss of precision can
                    // happen with big numbers however.
                    //
                    // The case where 'randomRange' is lower than zero is handled below,
                    // so there's no need to test against Number.MIN_SAFE_INTEGER.
                    let randomRange = maxInt.value - minInt.value + 1;
                    if (!isFinite(randomRange) || randomRange > Number.MAX_SAFE_INTEGER) {
                        randomRange = Number.MAX_SAFE_INTEGER;
                        this.Error("RANDOM was called with a range that exceeds the size that ink numbers can use.");
                    }
                    if (randomRange <= 0)
                        this.Error("RANDOM was called with minimum as " +
                            minInt.value +
                            " and maximum as " +
                            maxInt.value +
                            ". The maximum must be larger");
                    let resultSeed = this.state.storySeed + this.state.previousRandom;
                    let random = new PRNG_1.PRNG(resultSeed);
                    let nextRandom = random.next();
                    let chosenValue = (nextRandom % randomRange) + minInt.value;
                    this.state.PushEvaluationStack(new Value_1.IntValue(chosenValue));
                    // Next random number (rather than keeping the Random object around)
                    this.state.previousRandom = nextRandom;
                    break;
                }
                case ControlCommand_1.ControlCommand.CommandType.SeedRandom:
                    let seed = (0, TypeAssertion_1.asOrNull)(this.state.PopEvaluationStack(), Value_1.IntValue);
                    if (seed == null || seed instanceof Value_1.IntValue === false)
                        return this.Error("Invalid value passed to SEED_RANDOM");
                    // Originally a primitive type, but here, can be null.
                    // TODO: Replace by default value?
                    if (seed.value === null) {
                        return (0, NullException_1.throwNullException)("minInt.value");
                    }
                    this.state.storySeed = seed.value;
                    this.state.previousRandom = 0;
                    this.state.PushEvaluationStack(new Void_1.Void());
                    break;
                case ControlCommand_1.ControlCommand.CommandType.VisitIndex:
                    let count = this.state.VisitCountForContainer(this.state.currentPointer.container) - 1; // index not count
                    this.state.PushEvaluationStack(new Value_1.IntValue(count));
                    break;
                case ControlCommand_1.ControlCommand.CommandType.SequenceShuffleIndex:
                    let shuffleIndex = this.NextSequenceShuffleIndex();
                    this.state.PushEvaluationStack(new Value_1.IntValue(shuffleIndex));
                    break;
                case ControlCommand_1.ControlCommand.CommandType.StartThread:
                    // Handled in main step function
                    break;
                case ControlCommand_1.ControlCommand.CommandType.Done:
                    // We may exist in the context of the initial
                    // act of creating the thread, or in the context of
                    // evaluating the content.
                    if (this.state.callStack.canPopThread) {
                        this.state.callStack.PopThread();
                    }
                    // In normal flow - allow safe exit without warning
                    else {
                        this.state.didSafeExit = true;
                        // Stop flow in current thread
                        this.state.currentPointer = Pointer_1.Pointer.Null;
                    }
                    break;
                // Force flow to end completely
                case ControlCommand_1.ControlCommand.CommandType.End:
                    this.state.ForceEnd();
                    break;
                case ControlCommand_1.ControlCommand.CommandType.ListFromInt:
                    // var intVal = state.PopEvaluationStack () as IntValue;
                    let intVal = (0, TypeAssertion_1.asOrNull)(this.state.PopEvaluationStack(), Value_1.IntValue);
                    // var listNameVal = state.PopEvaluationStack () as StringValue;
                    let listNameVal = (0, TypeAssertion_1.asOrThrows)(this.state.PopEvaluationStack(), Value_1.StringValue);
                    if (intVal === null) {
                        throw new StoryException_1.StoryException("Passed non-integer when creating a list element from a numerical value.");
                    }
                    let generatedListValue = null;
                    if (this.listDefinitions === null) {
                        return (0, NullException_1.throwNullException)("this.listDefinitions");
                    }
                    let foundListDef = this.listDefinitions.TryListGetDefinition(listNameVal.value, null);
                    if (foundListDef.exists) {
                        // Originally a primitive type, but here, can be null.
                        // TODO: Replace by default value?
                        if (intVal.value === null) {
                            return (0, NullException_1.throwNullException)("minInt.value");
                        }
                        let foundItem = foundListDef.result.TryGetItemWithValue(intVal.value, InkList_1.InkListItem.Null);
                        if (foundItem.exists) {
                            generatedListValue = new Value_1.ListValue(foundItem.result, intVal.value);
                        }
                    }
                    else {
                        throw new StoryException_1.StoryException("Failed to find LIST called " + listNameVal.value);
                    }
                    if (generatedListValue == null)
                        generatedListValue = new Value_1.ListValue();
                    this.state.PushEvaluationStack(generatedListValue);
                    break;
                case ControlCommand_1.ControlCommand.CommandType.ListRange:
                    let max = (0, TypeAssertion_1.asOrNull)(this.state.PopEvaluationStack(), Value_1.Value);
                    let min = (0, TypeAssertion_1.asOrNull)(this.state.PopEvaluationStack(), Value_1.Value);
                    // var targetList = state.PopEvaluationStack () as ListValue;
                    let targetList = (0, TypeAssertion_1.asOrNull)(this.state.PopEvaluationStack(), Value_1.ListValue);
                    if (targetList === null || min === null || max === null)
                        throw new StoryException_1.StoryException("Expected list, minimum and maximum for LIST_RANGE");
                    if (targetList.value === null) {
                        return (0, NullException_1.throwNullException)("targetList.value");
                    }
                    let result = targetList.value.ListWithSubRange(min.valueObject, max.valueObject);
                    this.state.PushEvaluationStack(new Value_1.ListValue(result));
                    break;
                case ControlCommand_1.ControlCommand.CommandType.ListRandom: {
                    let listVal = this.state.PopEvaluationStack();
                    if (listVal === null)
                        throw new StoryException_1.StoryException("Expected list for LIST_RANDOM");
                    let list = listVal.value;
                    let newList = null;
                    if (list === null) {
                        throw (0, NullException_1.throwNullException)("list");
                    }
                    if (list.Count == 0) {
                        newList = new InkList_1.InkList();
                    }
                    else {
                        // Generate a random index for the element to take
                        let resultSeed = this.state.storySeed + this.state.previousRandom;
                        let random = new PRNG_1.PRNG(resultSeed);
                        let nextRandom = random.next();
                        let listItemIndex = nextRandom % list.Count;
                        // This bit is a little different from the original
                        // C# code, since iterators do not work in the same way.
                        // First, we iterate listItemIndex - 1 times, calling next().
                        // The listItemIndex-th time is made outside of the loop,
                        // in order to retrieve the value.
                        let listEnumerator = list.entries();
                        for (let i = 0; i <= listItemIndex - 1; i++) {
                            listEnumerator.next();
                        }
                        let value = listEnumerator.next().value;
                        let randomItem = {
                            Key: InkList_1.InkListItem.fromSerializedKey(value[0]),
                            Value: value[1],
                        };
                        // Origin list is simply the origin of the one element
                        if (randomItem.Key.originName === null) {
                            return (0, NullException_1.throwNullException)("randomItem.Key.originName");
                        }
                        newList = new InkList_1.InkList(randomItem.Key.originName, this);
                        newList.Add(randomItem.Key, randomItem.Value);
                        this.state.previousRandom = nextRandom;
                    }
                    this.state.PushEvaluationStack(new Value_1.ListValue(newList));
                    break;
                }
                default:
                    this.Error("unhandled ControlCommand: " + evalCommand);
                    break;
            }
            return true;
        }
        // Variable assignment
        else if (contentObj instanceof VariableAssignment_1.VariableAssignment) {
            let varAss = contentObj;
            let assignedVal = this.state.PopEvaluationStack();
            this.state.variablesState.Assign(varAss, assignedVal);
            return true;
        }
        // Variable reference
        else if (contentObj instanceof VariableReference_1.VariableReference) {
            let varRef = contentObj;
            let foundValue = null;
            // Explicit read count value
            if (varRef.pathForCount != null) {
                let container = varRef.containerForCount;
                let count = this.state.VisitCountForContainer(container);
                foundValue = new Value_1.IntValue(count);
            }
            // Normal variable reference
            else {
                foundValue = this.state.variablesState.GetVariableWithName(varRef.name);
                if (foundValue == null) {
                    this.Warning("Variable not found: '" +
                        varRef.name +
                        "'. Using default value of 0 (false). This can happen with temporary variables if the declaration hasn't yet been hit. Globals are always given a default value on load if a value doesn't exist in the save state.");
                    foundValue = new Value_1.IntValue(0);
                }
            }
            this.state.PushEvaluationStack(foundValue);
            return true;
        }
        // Native function call
        else if (contentObj instanceof NativeFunctionCall_1.NativeFunctionCall) {
            let func = contentObj;
            let funcParams = this.state.PopEvaluationStack(func.numberOfParameters);
            let result = func.Call(funcParams);
            this.state.PushEvaluationStack(result);
            return true;
        }
        // No control content, must be ordinary content
        return false;
    }
    ChoosePathString(path, resetCallstack = true, args = []) {
        this.IfAsyncWeCant("call ChoosePathString right now");
        if (this.onChoosePathString !== null)
            this.onChoosePathString(path, args);
        if (resetCallstack) {
            this.ResetCallstack();
        }
        else {
            if (this.state.callStack.currentElement.type == PushPop_1.PushPopType.Function) {
                let funcDetail = "";
                let container = this.state.callStack.currentElement.currentPointer.container;
                if (container != null) {
                    funcDetail = "(" + container.path.toString() + ") ";
                }
                throw new Error("Story was running a function " +
                    funcDetail +
                    "when you called ChoosePathString(" +
                    path +
                    ") - this is almost certainly not not what you want! Full stack trace: \n" +
                    this.state.callStack.callStackTrace);
            }
        }
        this.state.PassArgumentsToEvaluationStack(args);
        this.ChoosePath(new Path_1.Path(path));
    }
    IfAsyncWeCant(activityStr) {
        if (this._asyncContinueActive)
            throw new Error("Can't " +
                activityStr +
                ". Story is in the middle of a ContinueAsync(). Make more ContinueAsync() calls or a single Continue() call beforehand.");
    }
    ChoosePath(p, incrementingTurnIndex = true) {
        this.state.SetChosenPath(p, incrementingTurnIndex);
        // Take a note of newly visited containers for read counts etc
        this.VisitChangedContainersDueToDivert();
    }
    ChooseChoiceIndex(choiceIdx) {
        choiceIdx = choiceIdx;
        let choices = this.currentChoices;
        this.Assert(choiceIdx >= 0 && choiceIdx < choices.length, "choice out of range");
        let choiceToChoose = choices[choiceIdx];
        if (this.onMakeChoice !== null)
            this.onMakeChoice(choiceToChoose);
        if (choiceToChoose.threadAtGeneration === null) {
            return (0, NullException_1.throwNullException)("choiceToChoose.threadAtGeneration");
        }
        if (choiceToChoose.targetPath === null) {
            return (0, NullException_1.throwNullException)("choiceToChoose.targetPath");
        }
        this.state.callStack.currentThread = choiceToChoose.threadAtGeneration;
        this.ChoosePath(choiceToChoose.targetPath);
    }
    HasFunction(functionName) {
        try {
            return this.KnotContainerWithName(functionName) != null;
        }
        catch (e) {
            return false;
        }
    }
    EvaluateFunction(functionName, args = [], returnTextOutput = false) {
        // EvaluateFunction behaves slightly differently than the C# version.
        // In C#, you can pass a (second) parameter `out textOutput` to get the
        // text outputted by the function. This is not possible in js. Instead,
        // we maintain the regular signature (functionName, args), plus an
        // optional third parameter returnTextOutput. If set to true, we will
        // return both the textOutput and the returned value, as an object.
        if (this.onEvaluateFunction !== null)
            this.onEvaluateFunction(functionName, args);
        this.IfAsyncWeCant("evaluate a function");
        if (functionName == null) {
            throw new Error("Function is null");
        }
        else if (functionName == "" || functionName.trim() == "") {
            throw new Error("Function is empty or white space.");
        }
        let funcContainer = this.KnotContainerWithName(functionName);
        if (funcContainer == null) {
            throw new Error("Function doesn't exist: '" + functionName + "'");
        }
        let outputStreamBefore = [];
        outputStreamBefore.push(...this.state.outputStream);
        this._state.ResetOutput();
        this.state.StartFunctionEvaluationFromGame(funcContainer, args);
        // Evaluate the function, and collect the string output
        let stringOutput = new StringBuilder_1.StringBuilder();
        while (this.canContinue) {
            stringOutput.Append(this.Continue());
        }
        let textOutput = stringOutput.toString();
        this._state.ResetOutput(outputStreamBefore);
        let result = this.state.CompleteFunctionEvaluationFromGame();
        if (this.onCompleteEvaluateFunction != null)
            this.onCompleteEvaluateFunction(functionName, args, textOutput, result);
        return returnTextOutput ? { returned: result, output: textOutput } : result;
    }
    EvaluateExpression(exprContainer) {
        let startCallStackHeight = this.state.callStack.elements.length;
        this.state.callStack.Push(PushPop_1.PushPopType.Tunnel);
        this._temporaryEvaluationContainer = exprContainer;
        this.state.GoToStart();
        let evalStackHeight = this.state.evaluationStack.length;
        this.Continue();
        this._temporaryEvaluationContainer = null;
        // Should have fallen off the end of the Container, which should
        // have auto-popped, but just in case we didn't for some reason,
        // manually pop to restore the state (including currentPath).
        if (this.state.callStack.elements.length > startCallStackHeight) {
            this.state.PopCallStack();
        }
        let endStackHeight = this.state.evaluationStack.length;
        if (endStackHeight > evalStackHeight) {
            return this.state.PopEvaluationStack();
        }
        else {
            return null;
        }
    }
    CallExternalFunction(funcName, numberOfArguments) {
        if (funcName === null) {
            return (0, NullException_1.throwNullException)("funcName");
        }
        let funcDef = this._externals.get(funcName);
        let fallbackFunctionContainer = null;
        let foundExternal = typeof funcDef !== "undefined";
        if (foundExternal &&
            !funcDef.lookAheadSafe &&
            this._state.inStringEvaluation) {
            this.Error("External function " +
                funcName +
                ' could not be called because 1) it wasn\'t marked as lookaheadSafe when BindExternalFunction was called and 2) the story is in the middle of string generation, either because choice text is being generated, or because you have ink like "hello {func()}". You can work around this by generating the result of your function into a temporary variable before the string or choice gets generated: ~ temp x = ' +
                funcName +
                "()");
        }
        if (foundExternal &&
            !funcDef.lookAheadSafe &&
            this._stateSnapshotAtLastNewline !== null) {
            this._sawLookaheadUnsafeFunctionAfterNewline = true;
            return;
        }
        if (!foundExternal) {
            if (this.allowExternalFunctionFallbacks) {
                fallbackFunctionContainer = this.KnotContainerWithName(funcName);
                this.Assert(fallbackFunctionContainer !== null, "Trying to call EXTERNAL function '" +
                    funcName +
                    "' which has not been bound, and fallback ink function could not be found.");
                // Divert direct into fallback function and we're done
                this.state.callStack.Push(PushPop_1.PushPopType.Function, undefined, this.state.outputStream.length);
                this.state.divertedPointer = Pointer_1.Pointer.StartOf(fallbackFunctionContainer);
                return;
            }
            else {
                this.Assert(false, "Trying to call EXTERNAL function '" +
                    funcName +
                    "' which has not been bound (and ink fallbacks disabled).");
            }
        }
        // Pop arguments
        let args = [];
        for (let i = 0; i < numberOfArguments; ++i) {
            // var poppedObj = state.PopEvaluationStack () as Value;
            let poppedObj = (0, TypeAssertion_1.asOrThrows)(this.state.PopEvaluationStack(), Value_1.Value);
            let valueObj = poppedObj.valueObject;
            args.push(valueObj);
        }
        // Reverse arguments from the order they were popped,
        // so they're the right way round again.
        args.reverse();
        // Run the function!
        let funcResult = funcDef.function(args);
        // Convert return value (if any) to the a type that the ink engine can use
        let returnObj = null;
        if (funcResult != null) {
            returnObj = Value_1.Value.Create(funcResult);
            this.Assert(returnObj !== null, "Could not create ink value from returned object of type " +
                typeof funcResult);
        }
        else {
            returnObj = new Void_1.Void();
        }
        this.state.PushEvaluationStack(returnObj);
    }
    BindExternalFunctionGeneral(funcName, func, lookaheadSafe = true) {
        this.IfAsyncWeCant("bind an external function");
        this.Assert(!this._externals.has(funcName), "Function '" + funcName + "' has already been bound.");
        this._externals.set(funcName, {
            function: func,
            lookAheadSafe: lookaheadSafe,
        });
    }
    TryCoerce(value) {
        // We're skipping type coercition in this implementation. First of, js
        // is loosely typed, so it's not that important. Secondly, there is no
        // clean way (AFAIK) for the user to describe what type of parameters
        // they expect.
        return value;
    }
    BindExternalFunction(funcName, func, lookaheadSafe = false) {
        this.Assert(func != null, "Can't bind a null function");
        this.BindExternalFunctionGeneral(funcName, (args) => {
            this.Assert(args.length >= func.length, "External function expected " + func.length + " arguments");
            let coercedArgs = [];
            for (let i = 0, l = args.length; i < l; i++) {
                coercedArgs[i] = this.TryCoerce(args[i]);
            }
            return func.apply(null, coercedArgs);
        }, lookaheadSafe);
    }
    UnbindExternalFunction(funcName) {
        this.IfAsyncWeCant("unbind an external a function");
        this.Assert(this._externals.has(funcName), "Function '" + funcName + "' has not been bound.");
        this._externals.delete(funcName);
    }
    ValidateExternalBindings() {
        let c = null;
        let o = null;
        let missingExternals = arguments[1] || new Set();
        if (arguments[0] instanceof Container_1.Container) {
            c = arguments[0];
        }
        if (arguments[0] instanceof Object_1.InkObject) {
            o = arguments[0];
        }
        if (c === null && o === null) {
            this.ValidateExternalBindings(this._mainContentContainer, missingExternals);
            this._hasValidatedExternals = true;
            // No problem! Validation complete
            if (missingExternals.size == 0) {
                this._hasValidatedExternals = true;
            }
            else {
                let message = "Error: Missing function binding for external";
                message += missingExternals.size > 1 ? "s" : "";
                message += ": '";
                message += Array.from(missingExternals).join("', '");
                message += "' ";
                message += this.allowExternalFunctionFallbacks
                    ? ", and no fallback ink function found."
                    : " (ink fallbacks disabled)";
                this.Error(message);
            }
        }
        else if (c != null) {
            for (let innerContent of c.content) {
                let container = innerContent;
                if (container == null || !container.hasValidName)
                    this.ValidateExternalBindings(innerContent, missingExternals);
            }
            for (let [, value] of c.namedContent) {
                this.ValidateExternalBindings((0, TypeAssertion_1.asOrNull)(value, Object_1.InkObject), missingExternals);
            }
        }
        else if (o != null) {
            let divert = (0, TypeAssertion_1.asOrNull)(o, Divert_1.Divert);
            if (divert && divert.isExternal) {
                let name = divert.targetPathString;
                if (name === null) {
                    return (0, NullException_1.throwNullException)("name");
                }
                if (!this._externals.has(name)) {
                    if (this.allowExternalFunctionFallbacks) {
                        let fallbackFound = this.mainContentContainer.namedContent.has(name);
                        if (!fallbackFound) {
                            missingExternals.add(name);
                        }
                    }
                    else {
                        missingExternals.add(name);
                    }
                }
            }
        }
    }
    ObserveVariable(variableName, observer) {
        this.IfAsyncWeCant("observe a new variable");
        if (this._variableObservers === null)
            this._variableObservers = new Map();
        if (!this.state.variablesState.GlobalVariableExistsWithName(variableName))
            throw new Error("Cannot observe variable '" +
                variableName +
                "' because it wasn't declared in the ink story.");
        if (this._variableObservers.has(variableName)) {
            this._variableObservers.get(variableName).push(observer);
        }
        else {
            this._variableObservers.set(variableName, [observer]);
        }
    }
    ObserveVariables(variableNames, observers) {
        for (let i = 0, l = variableNames.length; i < l; i++) {
            this.ObserveVariable(variableNames[i], observers[i]);
        }
    }
    RemoveVariableObserver(observer, specificVariableName) {
        // A couple of things to know about this method:
        //
        // 1. Since `RemoveVariableObserver` is exposed to the JavaScript world,
        //    optionality is marked as `undefined` rather than `null`.
        //    To keep things simple, null-checks are performed using regular
        //    equality operators, where undefined == null.
        //
        // 2. Since C# delegates are translated to arrays of functions,
        //    -= becomes a call to splice and null-checks are replaced by
        //    emptiness-checks.
        //
        this.IfAsyncWeCant("remove a variable observer");
        if (this._variableObservers === null)
            return;
        if (specificVariableName != null) {
            if (this._variableObservers.has(specificVariableName)) {
                if (observer != null) {
                    let variableObservers = this._variableObservers.get(specificVariableName);
                    if (variableObservers != null) {
                        variableObservers.splice(variableObservers.indexOf(observer), 1);
                        if (variableObservers.length === 0) {
                            this._variableObservers.delete(specificVariableName);
                        }
                    }
                }
                else {
                    this._variableObservers.delete(specificVariableName);
                }
            }
        }
        else if (observer != null) {
            let keys = this._variableObservers.keys();
            for (let varName of keys) {
                let variableObservers = this._variableObservers.get(varName);
                if (variableObservers != null) {
                    variableObservers.splice(variableObservers.indexOf(observer), 1);
                    if (variableObservers.length === 0) {
                        this._variableObservers.delete(varName);
                    }
                }
            }
        }
    }
    VariableStateDidChangeEvent(variableName, newValueObj) {
        if (this._variableObservers === null)
            return;
        let observers = this._variableObservers.get(variableName);
        if (typeof observers !== "undefined") {
            if (!(newValueObj instanceof Value_1.Value)) {
                throw new Error("Tried to get the value of a variable that isn't a standard type");
            }
            // var val = newValueObj as Value;
            let val = (0, TypeAssertion_1.asOrThrows)(newValueObj, Value_1.Value);
            for (let observer of observers) {
                observer(variableName, val.valueObject);
            }
        }
    }
    get globalTags() {
        return this.TagsAtStartOfFlowContainerWithPathString("");
    }
    TagsForContentAtPath(path) {
        return this.TagsAtStartOfFlowContainerWithPathString(path);
    }
    TagsAtStartOfFlowContainerWithPathString(pathString) {
        let path = new Path_1.Path(pathString);
        let flowContainer = this.ContentAtPath(path).container;
        if (flowContainer === null) {
            return (0, NullException_1.throwNullException)("flowContainer");
        }
        while (true) {
            let firstContent = flowContainer.content[0];
            if (firstContent instanceof Container_1.Container)
                flowContainer = firstContent;
            else
                break;
        }
        let inTag = false;
        let tags = null;
        for (let c of flowContainer.content) {
            // var tag = c as Runtime.Tag;
            let command = (0, TypeAssertion_1.asOrNull)(c, ControlCommand_1.ControlCommand);
            if (command != null) {
                if (command.commandType == ControlCommand_1.ControlCommand.CommandType.BeginTag) {
                    inTag = true;
                }
                else if (command.commandType == ControlCommand_1.ControlCommand.CommandType.EndTag) {
                    inTag = false;
                }
            }
            else if (inTag) {
                let str = (0, TypeAssertion_1.asOrNull)(c, Value_1.StringValue);
                if (str !== null) {
                    if (tags === null)
                        tags = [];
                    if (str.value !== null)
                        tags.push(str.value);
                }
                else {
                    this.Error("Tag contained non-text content. Only plain text is allowed when using globalTags or TagsAtContentPath. If you want to evaluate dynamic content, you need to use story.Continue().");
                }
            }
            else {
                break;
            }
        }
        return tags;
    }
    BuildStringOfHierarchy() {
        let sb = new StringBuilder_1.StringBuilder();
        this.mainContentContainer.BuildStringOfHierarchy(sb, 0, this.state.currentPointer.Resolve());
        return sb.toString();
    }
    BuildStringOfContainer(container) {
        let sb = new StringBuilder_1.StringBuilder();
        container.BuildStringOfHierarchy(sb, 0, this.state.currentPointer.Resolve());
        return sb.toString();
    }
    NextContent() {
        this.state.previousPointer = this.state.currentPointer.copy();
        if (!this.state.divertedPointer.isNull) {
            this.state.currentPointer = this.state.divertedPointer.copy();
            this.state.divertedPointer = Pointer_1.Pointer.Null;
            this.VisitChangedContainersDueToDivert();
            if (!this.state.currentPointer.isNull) {
                return;
            }
        }
        let successfulPointerIncrement = this.IncrementContentPointer();
        if (!successfulPointerIncrement) {
            let didPop = false;
            if (this.state.callStack.CanPop(PushPop_1.PushPopType.Function)) {
                this.state.PopCallStack(PushPop_1.PushPopType.Function);
                if (this.state.inExpressionEvaluation) {
                    this.state.PushEvaluationStack(new Void_1.Void());
                }
                didPop = true;
            }
            else if (this.state.callStack.canPopThread) {
                this.state.callStack.PopThread();
                didPop = true;
            }
            else {
                this.state.TryExitFunctionEvaluationFromGame();
            }
            if (didPop && !this.state.currentPointer.isNull) {
                this.NextContent();
            }
        }
    }
    IncrementContentPointer() {
        let successfulIncrement = true;
        let pointer = this.state.callStack.currentElement.currentPointer.copy();
        pointer.index++;
        if (pointer.container === null) {
            return (0, NullException_1.throwNullException)("pointer.container");
        }
        while (pointer.index >= pointer.container.content.length) {
            successfulIncrement = false;
            // Container nextAncestor = pointer.container.parent as Container;
            let nextAncestor = (0, TypeAssertion_1.asOrNull)(pointer.container.parent, Container_1.Container);
            if (nextAncestor instanceof Container_1.Container === false) {
                break;
            }
            let indexInAncestor = nextAncestor.content.indexOf(pointer.container);
            if (indexInAncestor == -1) {
                break;
            }
            pointer = new Pointer_1.Pointer(nextAncestor, indexInAncestor);
            pointer.index++;
            successfulIncrement = true;
            if (pointer.container === null) {
                return (0, NullException_1.throwNullException)("pointer.container");
            }
        }
        if (!successfulIncrement)
            pointer = Pointer_1.Pointer.Null;
        this.state.callStack.currentElement.currentPointer = pointer.copy();
        return successfulIncrement;
    }
    TryFollowDefaultInvisibleChoice() {
        let allChoices = this._state.currentChoices;
        let invisibleChoices = allChoices.filter((c) => c.isInvisibleDefault);
        if (invisibleChoices.length == 0 ||
            allChoices.length > invisibleChoices.length)
            return false;
        let choice = invisibleChoices[0];
        if (choice.targetPath === null) {
            return (0, NullException_1.throwNullException)("choice.targetPath");
        }
        if (choice.threadAtGeneration === null) {
            return (0, NullException_1.throwNullException)("choice.threadAtGeneration");
        }
        this.state.callStack.currentThread = choice.threadAtGeneration;
        if (this._stateSnapshotAtLastNewline !== null) {
            this.state.callStack.currentThread = this.state.callStack.ForkThread();
        }
        this.ChoosePath(choice.targetPath, false);
        return true;
    }
    NextSequenceShuffleIndex() {
        // var numElementsIntVal = state.PopEvaluationStack () as IntValue;
        let numElementsIntVal = (0, TypeAssertion_1.asOrNull)(this.state.PopEvaluationStack(), Value_1.IntValue);
        if (!(numElementsIntVal instanceof Value_1.IntValue)) {
            this.Error("expected number of elements in sequence for shuffle index");
            return 0;
        }
        let seqContainer = this.state.currentPointer.container;
        if (seqContainer === null) {
            return (0, NullException_1.throwNullException)("seqContainer");
        }
        // Originally a primitive type, but here, can be null.
        // TODO: Replace by default value?
        if (numElementsIntVal.value === null) {
            return (0, NullException_1.throwNullException)("numElementsIntVal.value");
        }
        let numElements = numElementsIntVal.value;
        // var seqCountVal = state.PopEvaluationStack () as IntValue;
        let seqCountVal = (0, TypeAssertion_1.asOrThrows)(this.state.PopEvaluationStack(), Value_1.IntValue);
        let seqCount = seqCountVal.value;
        // Originally a primitive type, but here, can be null.
        // TODO: Replace by default value?
        if (seqCount === null) {
            return (0, NullException_1.throwNullException)("seqCount");
        }
        let loopIndex = seqCount / numElements;
        let iterationIndex = seqCount % numElements;
        let seqPathStr = seqContainer.path.toString();
        let sequenceHash = 0;
        for (let i = 0, l = seqPathStr.length; i < l; i++) {
            sequenceHash += seqPathStr.charCodeAt(i) || 0;
        }
        let randomSeed = sequenceHash + loopIndex + this.state.storySeed;
        let random = new PRNG_1.PRNG(Math.floor(randomSeed));
        let unpickedIndices = [];
        for (let i = 0; i < numElements; ++i) {
            unpickedIndices.push(i);
        }
        for (let i = 0; i <= iterationIndex; ++i) {
            let chosen = random.next() % unpickedIndices.length;
            let chosenIndex = unpickedIndices[chosen];
            unpickedIndices.splice(chosen, 1);
            if (i == iterationIndex) {
                return chosenIndex;
            }
        }
        throw new Error("Should never reach here");
    }
    Error(message, useEndLineNumber = false) {
        let e = new StoryException_1.StoryException(message);
        e.useEndLineNumber = useEndLineNumber;
        throw e;
    }
    Warning(message) {
        this.AddError(message, true);
    }
    AddError(message, isWarning = false, useEndLineNumber = false) {
        let dm = this.currentDebugMetadata;
        let errorTypeStr = isWarning ? "WARNING" : "ERROR";
        if (dm != null) {
            let lineNum = useEndLineNumber ? dm.endLineNumber : dm.startLineNumber;
            message =
                "RUNTIME " +
                    errorTypeStr +
                    ": '" +
                    dm.fileName +
                    "' line " +
                    lineNum +
                    ": " +
                    message;
        }
        else if (!this.state.currentPointer.isNull) {
            message =
                "RUNTIME " +
                    errorTypeStr +
                    ": (" +
                    this.state.currentPointer +
                    "): " +
                    message;
        }
        else {
            message = "RUNTIME " + errorTypeStr + ": " + message;
        }
        this.state.AddError(message, isWarning);
        // In a broken state don't need to know about any other errors.
        if (!isWarning)
            this.state.ForceEnd();
    }
    Assert(condition, message = null) {
        if (condition == false) {
            if (message == null) {
                message = "Story assert";
            }
            throw new Error(message + " " + this.currentDebugMetadata);
        }
    }
    get currentDebugMetadata() {
        let dm;
        let pointer = this.state.currentPointer;
        if (!pointer.isNull && pointer.Resolve() !== null) {
            dm = pointer.Resolve().debugMetadata;
            if (dm !== null) {
                return dm;
            }
        }
        for (let i = this.state.callStack.elements.length - 1; i >= 0; --i) {
            pointer = this.state.callStack.elements[i].currentPointer;
            if (!pointer.isNull && pointer.Resolve() !== null) {
                dm = pointer.Resolve().debugMetadata;
                if (dm !== null) {
                    return dm;
                }
            }
        }
        for (let i = this.state.outputStream.length - 1; i >= 0; --i) {
            let outputObj = this.state.outputStream[i];
            dm = outputObj.debugMetadata;
            if (dm !== null) {
                return dm;
            }
        }
        return null;
    }
    get mainContentContainer() {
        if (this._temporaryEvaluationContainer) {
            return this._temporaryEvaluationContainer;
        }
        else {
            return this._mainContentContainer;
        }
    }
}
exports.Story = Story;
Story.inkVersionCurrent = 21;
(function (Story) {
    let OutputStateChange;
    (function (OutputStateChange) {
        OutputStateChange[OutputStateChange["NoChange"] = 0] = "NoChange";
        OutputStateChange[OutputStateChange["ExtendedBeyondNewline"] = 1] = "ExtendedBeyondNewline";
        OutputStateChange[OutputStateChange["NewlineRemoved"] = 2] = "NewlineRemoved";
    })(OutputStateChange = Story.OutputStateChange || (Story.OutputStateChange = {}));
})(Story || (exports.Story = Story = {}));
//# sourceMappingURL=Story.js.map