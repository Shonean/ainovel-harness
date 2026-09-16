"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.Story = void 0;
const AuthorWarning_1 = require("./AuthorWarning");
const ConstantDeclaration_1 = require("./Declaration/ConstantDeclaration");
const Container_1 = require("../../../engine/Container");
const ControlCommand_1 = require("../../../engine/ControlCommand");
const ErrorType_1 = require("../ErrorType");
const FlowBase_1 = require("./Flow/FlowBase");
const FlowLevel_1 = require("./Flow/FlowLevel");
const IncludedFile_1 = require("./IncludedFile");
const ListDefinition_1 = require("./List/ListDefinition");
const ListElementDefinition_1 = require("./List/ListElementDefinition");
const Story_1 = require("../../../engine/Story");
const SymbolType_1 = require("./SymbolType");
const Text_1 = require("./Text");
const VariableAssignment_1 = require("../../../engine/VariableAssignment");
const TypeAssertion_1 = require("../../../engine/TypeAssertion");
const ClosestFlowBase_1 = require("./Flow/ClosestFlowBase");
const FunctionCall_1 = require("./FunctionCall");
const Path_1 = require("./Path");
class Story extends FlowBase_1.FlowBase {
    get flowLevel() {
        return FlowLevel_1.FlowLevel.Story;
    }
    get hadError() {
        return this._hadError;
    }
    get hadWarning() {
        return this._hadWarning;
    }
    constructor(toplevelObjects, isInclude = false) {
        // Don't do anything much on construction, leave it lightweight until
        // the ExportRuntime method is called.
        super(null, toplevelObjects, null, false, isInclude);
        this._errorHandler = null;
        this._hadError = false;
        this._hadWarning = false;
        this._dontFlattenContainers = new Set();
        this._listDefs = new Map();
        this.constants = new Map();
        this.externals = new Map();
        // Build setting for exporting:
        // When true, the visit count for *all* knots, stitches, choices,
        // and gathers is counted. When false, only those that are direclty
        // referenced by the ink are recorded. Use this flag to allow game-side
        // querying of  arbitrary knots/stitches etc.
        // Storing all counts is more robust and future proof (updates to the story file
        // that reference previously uncounted visits are possible, but generates a much
        // larger safe file, with a lot of potentially redundant counts.
        this.countAllVisits = false;
        this.ExportRuntime = (errorHandler = null) => {
            var _a, _b;
            this._errorHandler = errorHandler;
            // Find all constants before main export begins, so that VariableReferences know
            // whether to generate a runtime variable reference or the literal value
            this.constants = new Map();
            for (const constDecl of this.FindAll(ConstantDeclaration_1.ConstantDeclaration)()) {
                // Check for duplicate definitions
                const existingDefinition = this.constants.get(constDecl.constantName);
                if (existingDefinition) {
                    if (!existingDefinition.Equals(constDecl.expression)) {
                        const errorMsg = `CONST '${constDecl.constantName}' has been redefined with a different value. Multiple definitions of the same CONST are valid so long as they contain the same value. Initial definition was on ${existingDefinition.debugMetadata}.`;
                        this.Error(errorMsg, constDecl, false);
                    }
                }
                this.constants.set(constDecl.constantName, constDecl.expression);
            }
            // List definitions are treated like constants too - they should be usable
            // from other variable declarations.
            this._listDefs = new Map();
            for (const listDef of this.FindAll(ListDefinition_1.ListDefinition)()) {
                if ((_a = listDef.identifier) === null || _a === void 0 ? void 0 : _a.name) {
                    this._listDefs.set((_b = listDef.identifier) === null || _b === void 0 ? void 0 : _b.name, listDef);
                }
            }
            this.externals = new Map();
            // Resolution of weave point names has to come first, before any runtime code generation
            // since names have to be ready before diverts start getting created.
            // (It used to be done in the constructor for a weave, but didn't allow us to generate
            // errors when name resolution failed.)
            this.ResolveWeavePointNaming();
            // Get default implementation of runtimeObject, which calls ContainerBase's generation method
            const rootContainer = this.runtimeObject;
            // Export initialisation of global variables
            // TODO: We *could* add this as a declarative block to the story itself...
            const variableInitialisation = new Container_1.Container();
            variableInitialisation.AddContent(ControlCommand_1.ControlCommand.EvalStart());
            // Global variables are those that are local to the story and marked as global
            const runtimeLists = [];
            for (const [key, value] of this.variableDeclarations) {
                if (value.isGlobalDeclaration) {
                    if (value.listDefinition) {
                        this._listDefs.set(key, value.listDefinition);
                        variableInitialisation.AddContent(value.listDefinition.runtimeObject);
                        runtimeLists.push(value.listDefinition.runtimeListDefinition);
                    }
                    else {
                        if (!value.expression) {
                            throw new Error();
                        }
                        value.expression.GenerateIntoContainer(variableInitialisation);
                    }
                    const runtimeVarAss = new VariableAssignment_1.VariableAssignment(key, true);
                    runtimeVarAss.isGlobal = true;
                    variableInitialisation.AddContent(runtimeVarAss);
                }
            }
            variableInitialisation.AddContent(ControlCommand_1.ControlCommand.EvalEnd());
            variableInitialisation.AddContent(ControlCommand_1.ControlCommand.End());
            if (this.variableDeclarations.size > 0) {
                variableInitialisation.name = "global decl";
                rootContainer.AddToNamedContentOnly(variableInitialisation);
            }
            // Signal that it's safe to exit without error, even if there are no choices generated
            // (this only happens at the end of top level content that isn't in any particular knot)
            rootContainer.AddContent(ControlCommand_1.ControlCommand.Done());
            // Replace runtimeObject with Story object instead of the Runtime.Container generated by Parsed.ContainerBase
            const runtimeStory = new Story_1.Story(rootContainer, runtimeLists);
            this.runtimeObject = runtimeStory;
            if (this.hadError) {
                return null;
            }
            // Optimisation step - inline containers that can be
            this.FlattenContainersIn(rootContainer);
            // Now that the story has been fulled parsed into a hierarchy,
            // and the derived runtime hierarchy has been built, we can
            // resolve referenced symbols such as variables and paths.
            // e.g. for paths " -> knotName --> stitchName" into an INKPath (knotName.stitchName)
            // We don't make any assumptions that the INKPath follows the same
            // conventions as the script format, so we resolve to actual objects before
            // translating into an INKPath. (This also allows us to choose whether
            // we want the paths to be absolute)
            this.ResolveReferences(this);
            if (this.hadError) {
                return null;
            }
            runtimeStory.ResetState();
            return runtimeStory;
        };
        this.ResolveList = (listName) => {
            let list = this._listDefs.get(listName);
            if (!list) {
                return null;
            }
            return list;
        };
        this.ResolveListItem = (listName, itemName, source = null) => {
            let listDef = null;
            // Search a specific list if we know its name (i.e. the form listName.itemName)
            if (listName) {
                if (!(listDef = this._listDefs.get(listName))) {
                    return null;
                }
                return listDef.ItemNamed(itemName);
            }
            else {
                // Otherwise, try to search all lists
                let foundItem = null;
                let originalFoundList = null;
                for (const [, value] of this._listDefs.entries()) {
                    const itemInThisList = value.ItemNamed(itemName);
                    if (itemInThisList) {
                        if (foundItem) {
                            this.Error(`Ambiguous item name '${itemName}' found in multiple sets, including ${originalFoundList.identifier} and ${value.identifier}`, source, false);
                        }
                        else {
                            foundItem = itemInThisList;
                            originalFoundList = value;
                        }
                    }
                }
                return foundItem;
            }
        };
        this.FlattenContainersIn = (container) => {
            // Need to create a collection to hold the inner containers
            // because otherwise we'd end up modifying during iteration
            const innerContainers = new Set();
            if (container.content) {
                for (const c of container.content) {
                    const innerContainer = (0, TypeAssertion_1.asOrNull)(c, Container_1.Container);
                    if (innerContainer) {
                        innerContainers.add(innerContainer);
                    }
                }
            }
            // Can't flatten the named inner containers, but we can at least
            // iterate through their children
            if (container.namedContent) {
                for (const [, value] of container.namedContent) {
                    const namedInnerContainer = (0, TypeAssertion_1.asOrNull)(value, Container_1.Container);
                    if (namedInnerContainer) {
                        innerContainers.add(namedInnerContainer);
                    }
                }
            }
            for (const innerContainer of innerContainers) {
                this.TryFlattenContainer(innerContainer);
                this.FlattenContainersIn(innerContainer);
            }
        };
        this.TryFlattenContainer = (container) => {
            if ((container.namedContent && container.namedContent.size > 0) ||
                container.hasValidName ||
                this._dontFlattenContainers.has(container)) {
                return;
            }
            // Inline all the content in container into the parent
            const parentContainer = (0, TypeAssertion_1.asOrNull)(container.parent, Container_1.Container);
            if (parentContainer) {
                let contentIdx = parentContainer.content.indexOf(container);
                parentContainer.content.splice(contentIdx, 1);
                const dm = container.ownDebugMetadata;
                if (container.content) {
                    for (const innerContent of container.content) {
                        innerContent.parent = null;
                        if (dm !== null && innerContent.ownDebugMetadata === null) {
                            innerContent.debugMetadata = dm;
                        }
                        parentContainer.InsertContent(innerContent, contentIdx);
                        contentIdx += 1;
                    }
                }
            }
        };
        this.Error = (message, source, isWarning) => {
            let errorType = isWarning ? ErrorType_1.ErrorType.Warning : ErrorType_1.ErrorType.Error;
            let sb = "";
            if (source instanceof AuthorWarning_1.AuthorWarning) {
                sb += "TODO: ";
                errorType = ErrorType_1.ErrorType.Author;
            }
            else if (isWarning) {
                sb += "WARNING: ";
            }
            else {
                sb += "ERROR: ";
            }
            if (source &&
                source.debugMetadata !== null &&
                source.debugMetadata.startLineNumber >= 1) {
                if (source.debugMetadata.fileName != null) {
                    sb += `'${source.debugMetadata.fileName}' `;
                }
                sb += `line ${source.debugMetadata.startLineNumber}: `;
            }
            sb += message;
            message = sb;
            if (this._errorHandler !== null) {
                this._errorHandler(message, errorType);
            }
            else {
                throw new Error(message);
            }
            this._hadError = errorType === ErrorType_1.ErrorType.Error;
            this._hadWarning = errorType === ErrorType_1.ErrorType.Warning;
        };
        this.ResetError = () => {
            this._hadError = false;
            this._hadWarning = false;
        };
        this.IsExternal = (namedFuncTarget) => this.externals.has(namedFuncTarget);
        this.AddExternal = (decl) => {
            if (this.externals.has(decl.name)) {
                this.Error(`Duplicate EXTERNAL definition of '${decl.name}'`, decl, false);
            }
            else if (decl.name) {
                this.externals.set(decl.name, decl);
            }
        };
        this.DontFlattenContainer = (container) => {
            this._dontFlattenContainers.add(container);
        };
        this.NameConflictError = (obj, name, existingObj, typeNameToPrint) => {
            obj.Error(`${typeNameToPrint} '${name}': name has already been used for a ${existingObj.typeName.toLowerCase()} on ${existingObj.debugMetadata}`);
        };
        // Check given symbol type against everything that's of a higher priority in the ordered SymbolType enum (above).
        // When the given symbol type level is reached, we early-out / return.
        this.CheckForNamingCollisions = (obj, identifier, symbolType, typeNameOverride = "") => {
            var _a;
            const typeNameToPrint = typeNameOverride || obj.typeName;
            if (Story.IsReservedKeyword(identifier === null || identifier === void 0 ? void 0 : identifier.name)) {
                obj.Error(`'${identifier}' cannot be used for the name of a ${typeNameToPrint.toLowerCase()} because it's a reserved keyword`);
                return;
            }
            else if (FunctionCall_1.FunctionCall.IsBuiltIn((identifier === null || identifier === void 0 ? void 0 : identifier.name) || "")) {
                obj.Error(`'${identifier}' cannot be used for the name of a ${typeNameToPrint.toLowerCase()} because it's a built in function`);
                return;
            }
            // Top level knots
            const maybeKnotOrFunction = this.ContentWithNameAtLevel((identifier === null || identifier === void 0 ? void 0 : identifier.name) || "", FlowLevel_1.FlowLevel.Knot);
            const knotOrFunction = (0, TypeAssertion_1.asOrNull)(maybeKnotOrFunction, FlowBase_1.FlowBase);
            if (knotOrFunction &&
                (knotOrFunction !== obj || symbolType === SymbolType_1.SymbolType.Arg)) {
                this.NameConflictError(obj, (identifier === null || identifier === void 0 ? void 0 : identifier.name) || "", knotOrFunction, typeNameToPrint);
                return;
            }
            if (symbolType < SymbolType_1.SymbolType.List) {
                return;
            }
            // Lists
            for (const [key, value] of this._listDefs) {
                if ((identifier === null || identifier === void 0 ? void 0 : identifier.name) === key &&
                    obj !== value &&
                    value.variableAssignment !== obj) {
                    this.NameConflictError(obj, identifier === null || identifier === void 0 ? void 0 : identifier.name, value, typeNameToPrint);
                }
                // We don't check for conflicts between individual elements in
                // different lists because they are namespaced.
                if (!(obj instanceof ListElementDefinition_1.ListElementDefinition)) {
                    for (const item of value.itemDefinitions) {
                        if ((identifier === null || identifier === void 0 ? void 0 : identifier.name) === item.name) {
                            this.NameConflictError(obj, (identifier === null || identifier === void 0 ? void 0 : identifier.name) || "", item, typeNameToPrint);
                        }
                    }
                }
            }
            // Don't check for VAR->VAR conflicts because that's handled separately
            // (necessary since checking looks up in a dictionary)
            if (symbolType <= SymbolType_1.SymbolType.Var) {
                return;
            }
            // Global variable collision
            const varDecl = ((identifier === null || identifier === void 0 ? void 0 : identifier.name) && this.variableDeclarations.get(identifier === null || identifier === void 0 ? void 0 : identifier.name)) ||
                null;
            if (varDecl &&
                varDecl !== obj &&
                varDecl.isGlobalDeclaration &&
                varDecl.listDefinition == null) {
                this.NameConflictError(obj, (identifier === null || identifier === void 0 ? void 0 : identifier.name) || "", varDecl, typeNameToPrint);
            }
            if (symbolType < SymbolType_1.SymbolType.SubFlowAndWeave) {
                return;
            }
            // Stitches, Choices and Gathers
            const path = new Path_1.Path(identifier);
            const targetContent = path.ResolveFromContext(obj);
            if (targetContent && targetContent !== obj) {
                this.NameConflictError(obj, (identifier === null || identifier === void 0 ? void 0 : identifier.name) || "", targetContent, typeNameToPrint);
                return;
            }
            if (symbolType < SymbolType_1.SymbolType.Arg) {
                return;
            }
            // Arguments to the current flow
            if (symbolType !== SymbolType_1.SymbolType.Arg) {
                let flow = (0, TypeAssertion_1.asOrNull)(obj, FlowBase_1.FlowBase);
                if (!flow) {
                    flow = (0, ClosestFlowBase_1.ClosestFlowBase)(obj);
                }
                if (flow && flow.hasParameters && flow.args) {
                    for (const arg of flow.args) {
                        if (((_a = arg.identifier) === null || _a === void 0 ? void 0 : _a.name) === (identifier === null || identifier === void 0 ? void 0 : identifier.name)) {
                            obj.Error(`${typeNameToPrint} '${identifier}': name has already been used for a argument to ${flow.identifier} on ${flow.debugMetadata}`);
                            return;
                        }
                    }
                }
            }
        };
    }
    get typeName() {
        return "Story";
    }
    // Before this function is called, we have IncludedFile objects interspersed
    // in our content wherever an include statement was.
    // So that the include statement can be added in a sensible place (e.g. the
    // top of the file) without side-effects of jumping into a knot that was
    // defined in that include, we separate knots and stitches from anything
    // else defined at the top scope of the included file.
    //
    // Algorithm: For each IncludedFile we find, split its contents into
    // knots/stiches and any other content. Insert the normal content wherever
    // the include statement was, and append the knots/stitches to the very
    // end of the main story.
    PreProcessTopLevelObjects(topLevelContent) {
        super.PreProcessTopLevelObjects(topLevelContent);
        const flowsFromOtherFiles = [];
        // Inject included files
        for (let obj of topLevelContent) {
            if (obj instanceof IncludedFile_1.IncludedFile) {
                const file = obj;
                // Remove the IncludedFile itself
                const posOfObj = topLevelContent.indexOf(obj);
                topLevelContent.splice(posOfObj, 1);
                // When an included story fails to load, the include
                // line itself is still valid, so we have to handle it here
                if (file.includedStory) {
                    const nonFlowContent = [];
                    const subStory = file.includedStory;
                    // Allow empty file
                    if (subStory.content != null) {
                        for (const subStoryObj of subStory.content) {
                            if (subStoryObj instanceof FlowBase_1.FlowBase) {
                                flowsFromOtherFiles.push(subStoryObj);
                            }
                            else {
                                nonFlowContent.push(subStoryObj);
                            }
                        }
                        // Add newline on the end of the include
                        nonFlowContent.push(new Text_1.Text("\n"));
                        // Add contents of the file in its place
                        topLevelContent.splice(posOfObj, 0, ...nonFlowContent);
                        // Skip past the content of this sub story
                        // (since it will already have recursively included
                        //  any lines from other files)
                    }
                }
                // Include object has been removed, with possible content inserted,
                // and position of 'i' will have been determined already.
                continue;
            }
        }
        // Add the flows we collected from the included files to the
        // end of our list of our content
        topLevelContent.splice(0, 0, ...flowsFromOtherFiles);
    }
}
exports.Story = Story;
Story.IsReservedKeyword = (name) => {
    switch (name) {
        case "true":
        case "false":
        case "not":
        case "return":
        case "else":
        case "VAR":
        case "CONST":
        case "temp":
        case "LIST":
        case "function":
            return true;
    }
    return false;
};
//# sourceMappingURL=Story.js.map