"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.InkParser = void 0;
const Argument_1 = require("./ParsedHierarchy/Argument");
const AuthorWarning_1 = require("./ParsedHierarchy/AuthorWarning");
const BinaryExpression_1 = require("./ParsedHierarchy/Expression/BinaryExpression");
const CharacterRange_1 = require("./CharacterRange");
const CharacterSet_1 = require("./CharacterSet");
const Choice_1 = require("./ParsedHierarchy/Choice");
const CommentEliminator_1 = require("./CommentEliminator");
const Conditional_1 = require("./ParsedHierarchy/Conditional/Conditional");
const ConditionalSingleBranch_1 = require("./ParsedHierarchy/Conditional/ConditionalSingleBranch");
const ContentList_1 = require("./ParsedHierarchy/ContentList");
const ConstantDeclaration_1 = require("./ParsedHierarchy/Declaration/ConstantDeclaration");
const CustomFlags_1 = require("./CustomFlags");
const DebugMetadata_1 = require("../../engine/DebugMetadata");
const Divert_1 = require("./ParsedHierarchy/Divert/Divert");
const DivertTarget_1 = require("./ParsedHierarchy/Divert/DivertTarget");
const Expression_1 = require("./ParsedHierarchy/Expression/Expression");
const ExternalDeclaration_1 = require("./ParsedHierarchy/Declaration/ExternalDeclaration");
const FlowDecl_1 = require("./FlowDecl");
const FunctionCall_1 = require("./ParsedHierarchy/FunctionCall");
const Gather_1 = require("./ParsedHierarchy/Gather/Gather");
const Glue_1 = require("./ParsedHierarchy/Glue");
const Glue_2 = require("../../engine/Glue");
const IncDecExpression_1 = require("./ParsedHierarchy/Expression/IncDecExpression");
const IncludedFile_1 = require("./ParsedHierarchy/IncludedFile");
const InfixOperator_1 = require("./InfixOperator");
const Knot_1 = require("./ParsedHierarchy/Knot");
const List_1 = require("./ParsedHierarchy/List/List");
const ListDefinition_1 = require("./ParsedHierarchy/List/ListDefinition");
const ListElementDefinition_1 = require("./ParsedHierarchy/List/ListElementDefinition");
const MultipleConditionExpression_1 = require("./ParsedHierarchy/Expression/MultipleConditionExpression");
const Object_1 = require("./ParsedHierarchy/Object");
const Path_1 = require("./ParsedHierarchy/Path");
const ReturnType_1 = require("./ParsedHierarchy/ReturnType");
const Sequence_1 = require("./ParsedHierarchy/Sequence/Sequence");
const SequenceType_1 = require("./ParsedHierarchy/Sequence/SequenceType");
const StatementLevel_1 = require("./StatementLevel");
const Stitch_1 = require("./ParsedHierarchy/Stitch");
const Story_1 = require("./ParsedHierarchy/Story");
const StringExpression_1 = require("./ParsedHierarchy/Expression/StringExpression");
const StringParser_1 = require("./StringParser/StringParser");
const Tag_1 = require("./ParsedHierarchy/Tag");
const Text_1 = require("./ParsedHierarchy/Text");
const TunnelOnwards_1 = require("./ParsedHierarchy/TunnelOnwards");
const VariableAssignment_1 = require("./ParsedHierarchy/Variable/VariableAssignment");
const VariableReference_1 = require("./ParsedHierarchy/Variable/VariableReference");
const UnaryExpression_1 = require("./ParsedHierarchy/Expression/UnaryExpression");
const TypeAssertion_1 = require("../../engine/TypeAssertion");
const Identifier_1 = require("./ParsedHierarchy/Identifier");
const NumberExpression_1 = require("./ParsedHierarchy/Expression/NumberExpression");
const ErrorType_1 = require("./ErrorType");
const DefaultFileHandler_1 = require("../FileHandler/DefaultFileHandler");
class InkParser extends StringParser_1.StringParser {
    /**
     * Begin base InkParser section.
     */
    get fileHandler() {
        if (!this._fileHandler) {
            throw new Error("No FileHandler defined");
        }
        return this._fileHandler;
    }
    set fileHandler(value) {
        this._fileHandler = value;
    }
    constructor(str, filename = null, externalErrorHandler = null, rootParser = null, fileHandler = null) {
        super(str);
        // Main entry point
        // NOTE: This method is named Parse() in upstream.
        this.ParseStory = () => {
            const topLevelContent = this.StatementsAtLevel(StatementLevel_1.StatementLevel.Top);
            // Note we used to return null if there were any errors, but this would mean
            // that include files would return completely empty rather than attempting to
            // continue with errors. Returning an empty include files meant that anything
            // that *did* compile successfully would otherwise be ignored, generating way
            // more errors than necessary.
            return new Story_1.Story(topLevelContent, this._rootParser !== this);
        };
        this.SeparatedList = (mainRule, separatorRule) => {
            const firstElement = this.Parse(mainRule);
            if (firstElement === null) {
                return null;
            }
            const allElements = [];
            allElements.push(firstElement);
            do {
                const nextElementRuleId = this.BeginRule();
                let sep = separatorRule();
                if (sep === null) {
                    this.FailRule(nextElementRuleId);
                    break;
                }
                const nextElement = this.Parse(mainRule);
                if (nextElement === null) {
                    this.FailRule(nextElementRuleId);
                    break;
                }
                this.SucceedRule(nextElementRuleId);
                allElements.push(nextElement);
            } while (true);
            return allElements;
        };
        this.CreateDebugMetadata = (stateAtStart, stateAtEnd) => {
            const md = new DebugMetadata_1.DebugMetadata();
            md.startLineNumber = ((stateAtStart === null || stateAtStart === void 0 ? void 0 : stateAtStart.lineIndex) || 0) + 1;
            md.endLineNumber = stateAtEnd.lineIndex + 1;
            md.startCharacterNumber = ((stateAtStart === null || stateAtStart === void 0 ? void 0 : stateAtStart.characterInLineIndex) || 0) + 1;
            md.endCharacterNumber = stateAtEnd.characterInLineIndex + 1;
            md.fileName = this._filename;
            return md;
        };
        this.RuleDidSucceed = (result, stateAtStart, stateAtEnd) => {
            // Apply DebugMetadata based on the state at the start of the rule
            // (i.e. use line number as it was at the start of the rule)
            const parsedObj = (0, TypeAssertion_1.asOrNull)(result, Object_1.ParsedObject);
            if (parsedObj) {
                parsedObj.debugMetadata = this.CreateDebugMetadata(stateAtStart, stateAtEnd);
            }
            // A list of objects that doesn't already have metadata?
            const parsedListObjs = Array.isArray(result)
                ? result
                : null;
            if (parsedListObjs !== null) {
                for (const parsedListObj of parsedListObjs) {
                    const singleObj = (0, TypeAssertion_1.asOrNull)(parsedListObj, Object_1.ParsedObject);
                    if (!singleObj)
                        continue;
                    if (!parsedListObj.hasOwnDebugMetadata) {
                        parsedListObj.debugMetadata = this.CreateDebugMetadata(stateAtStart, stateAtEnd);
                    }
                }
            }
            const id = (0, TypeAssertion_1.asOrNull)(result, Identifier_1.Identifier);
            if (id != null) {
                id.debugMetadata = this.CreateDebugMetadata(stateAtStart, stateAtEnd);
            }
        };
        this.OnStringParserError = (message, index, lineIndex = 0, isWarning = false) => {
            const warningType = isWarning ? "WARNING:" : "ERROR:";
            let fullMessage = warningType;
            if (this._filename !== null) {
                fullMessage += ` '${this._filename}'`;
            }
            fullMessage += ` line ${lineIndex + 1}: ${message}`;
            if (this._externalErrorHandler !== null) {
                this._externalErrorHandler(fullMessage, isWarning ? ErrorType_1.ErrorType.Warning : ErrorType_1.ErrorType.Error);
            }
            else {
                throw new Error(fullMessage);
            }
        };
        this.AuthorWarning = () => {
            this.Whitespace();
            const identifier = this.Parse(this.IdentifierWithMetadata);
            if (identifier === null || identifier.name !== "TODO") {
                return null;
            }
            this.Whitespace();
            this.ParseString(":");
            this.Whitespace();
            const message = this.ParseUntilCharactersFromString("\n\r");
            if (message) {
                return new AuthorWarning_1.AuthorWarning(message);
            }
            return null;
        };
        this.ExtendIdentifierCharacterRanges = (identifierCharSet) => {
            const characterRanges = InkParser.ListAllCharacterRanges();
            for (const charRange of characterRanges) {
                identifierCharSet.AddCharacters(charRange.ToCharacterSet());
            }
        };
        /**
         * End CharacterRanges section.
         */
        /**
         * Begin Choices section.
         */
        this._parsingChoice = false;
        this.Choice = () => {
            let onceOnlyChoice = true;
            let bullets = this.Interleave(this.OptionalExclude(this.Whitespace), this.String("*"));
            if (!bullets) {
                bullets = this.Interleave(this.OptionalExclude(this.Whitespace), this.String("+"));
                if (bullets === null) {
                    return null;
                }
                onceOnlyChoice = false;
            }
            // Optional name for the choice
            const optionalName = this.Parse(this.BracketedName);
            this.Whitespace();
            // Allow optional newline right after a choice name
            if (optionalName != null)
                this.Newline();
            // Optional condition for whether the choice should be shown to the player
            const conditionExpr = this.Parse(this.ChoiceCondition);
            this.Whitespace();
            // Ordinarily we avoid parser state variables like these, since
            // nesting would require us to store them in a stack. But since you should
            // never be able to nest choices within choice content, it's fine here.
            if (this._parsingChoice) {
                throw new Error("Already parsing a choice - shouldn't have nested choices");
            }
            this._parsingChoice = true;
            let startContent = null;
            const startTextAndLogic = this.Parse(this.MixedTextAndLogic);
            if (startTextAndLogic) {
                startContent = new ContentList_1.ContentList(startTextAndLogic);
            }
            let optionOnlyContent = null;
            let innerContent = null;
            // Check for a the weave style format:
            //   * "Hello[."]," he said.
            const hasWeaveStyleInlineBrackets = this.ParseString("[") !== null;
            if (hasWeaveStyleInlineBrackets) {
                this.EndTagIfNecessary(startContent);
                const optionOnlyTextAndLogic = this.Parse(this.MixedTextAndLogic);
                if (optionOnlyTextAndLogic !== null) {
                    optionOnlyContent = new ContentList_1.ContentList(optionOnlyTextAndLogic);
                }
                this.Expect(this.String("]"), "closing ']' for weave-style option");
                this.EndTagIfNecessary(optionOnlyContent);
                let innerTextAndLogic = this.Parse(this.MixedTextAndLogic);
                if (innerTextAndLogic !== null) {
                    innerContent = new ContentList_1.ContentList(innerTextAndLogic);
                }
            }
            this.Whitespace();
            this.EndTagIfNecessary(innerContent !== null && innerContent !== void 0 ? innerContent : startContent);
            // Finally, now we know we're at the end of the main choice body, parse
            // any diverts separately.
            const diverts = this.Parse(this.MultiDivert);
            this._parsingChoice = false;
            this.Whitespace();
            // Completely empty choice without even an empty divert?
            const emptyContent = !startContent && !innerContent && !optionOnlyContent;
            if (emptyContent && diverts === null) {
                this.Warning("Choice is completely empty. Interpretting as a default fallback choice. Add a divert arrow to remove this warning: * ->");
            }
            if (!startContent && hasWeaveStyleInlineBrackets && !optionOnlyContent) {
                // * [] some text
                this.Warning("Blank choice - if you intended a default fallback choice, use the `* ->` syntax");
            }
            if (!innerContent) {
                innerContent = new ContentList_1.ContentList();
            }
            this.EndTagIfNecessary(innerContent);
            // Normal diverts on the end of a choice - simply add to the normal content
            if (diverts !== null) {
                for (const divObj of diverts) {
                    // may be TunnelOnwards
                    const div = (0, TypeAssertion_1.asOrNull)(divObj, Divert_1.Divert);
                    // Empty divert serves no purpose other than to say
                    // "this choice is intentionally left blank"
                    // (as an invisible default choice)
                    if (div && div.isEmpty) {
                        continue;
                    }
                    innerContent.AddContent(divObj);
                }
            }
            // Terminate main content with a newline since this is the end of the line
            // Note that this will be redundant if the diverts above definitely take
            // the flow away permanently.
            innerContent.AddContent(new Text_1.Text("\n"));
            const choice = new Choice_1.Choice(startContent, optionOnlyContent, innerContent);
            if (optionalName)
                choice.identifier = optionalName;
            choice.indentationDepth = bullets.length;
            choice.hasWeaveStyleInlineBrackets = hasWeaveStyleInlineBrackets;
            choice.condition = conditionExpr;
            choice.onceOnly = onceOnlyChoice;
            choice.isInvisibleDefault = emptyContent;
            return choice;
        };
        this.ChoiceCondition = () => {
            const conditions = this.Interleave(this.ChoiceSingleCondition, this.ChoiceConditionsSpace);
            if (conditions === null) {
                return null;
            }
            else if (conditions.length === 1) {
                return conditions[0];
            }
            return new MultipleConditionExpression_1.MultipleConditionExpression(conditions);
        };
        this.ChoiceConditionsSpace = () => {
            // Both optional
            // Newline includes initial end of line whitespace
            this.Newline();
            this.Whitespace();
            return StringParser_1.ParseSuccess;
        };
        this.ChoiceSingleCondition = () => {
            if (this.ParseString("{") === null) {
                return null;
            }
            const condExpr = this.Expect(this.Expression, "choice condition inside { }");
            this.DisallowIncrement(condExpr);
            this.Expect(this.String("}"), "closing '}' for choice condition");
            return condExpr;
        };
        this.Gather = () => {
            const gatherDashCountObj = this.Parse(this.GatherDashes);
            if (gatherDashCountObj === null) {
                return null;
            }
            const gatherDashCount = Number(gatherDashCountObj);
            // Optional name for the gather
            const optionalName = this.Parse(this.BracketedName);
            const gather = new Gather_1.Gather(optionalName, gatherDashCount);
            // Optional newline before gather's content begins
            this.Newline();
            return gather;
        };
        this.GatherDashes = () => {
            this.Whitespace();
            let gatherDashCount = 0;
            while (this.ParseDashNotArrow() !== null) {
                gatherDashCount += 1;
                this.Whitespace();
            }
            if (gatherDashCount === 0) {
                return null;
            }
            return gatherDashCount;
        };
        this.ParseDashNotArrow = () => {
            const ruleId = this.BeginRule();
            if (this.ParseString("->") === null &&
                this.ParseSingleCharacter() === "-") {
                return this.SucceedRule(ruleId);
            }
            return this.FailRule(ruleId);
        };
        this.BracketedName = () => {
            if (this.ParseString("(") === null) {
                return null;
            }
            this.Whitespace();
            const name = this.Parse(this.IdentifierWithMetadata);
            if (name === null) {
                return null;
            }
            this.Whitespace();
            this.Expect(this.String(")"), "closing ')' for bracketed name");
            return name;
        };
        /**
         * End Choices section.
         */
        /**
         * Begin Conditional section.
         */
        this.InnerConditionalContent = (initialQueryExpression) => {
            if (initialQueryExpression === undefined) {
                const initialQueryExpression = this.Parse(this.ConditionExpression);
                const conditional = this.Parse(() => this.InnerConditionalContent(initialQueryExpression));
                if (conditional === null) {
                    return null;
                }
                return conditional;
            }
            let alternatives;
            const canBeInline = initialQueryExpression !== null;
            const isInline = this.Parse(this.Newline) === null;
            if (isInline && !canBeInline) {
                return null;
            }
            if (isInline) {
                // Inline innards
                alternatives = this.InlineConditionalBranches();
            }
            else {
                // Multiline innards
                alternatives = this.MultilineConditionalBranches();
                if (alternatives === null) {
                    // Allow single piece of content within multi-line expression, e.g.:
                    // { true:
                    //    Some content that isn't preceded by '-'
                    // }
                    if (initialQueryExpression) {
                        let soleContent = this.StatementsAtLevel(StatementLevel_1.StatementLevel.InnerBlock);
                        if (soleContent !== null) {
                            const soleBranch = new ConditionalSingleBranch_1.ConditionalSingleBranch(soleContent);
                            alternatives = [soleBranch];
                            // Also allow a final "- else:" clause
                            const elseBranch = this.Parse(this.SingleMultilineCondition);
                            if (elseBranch) {
                                if (!elseBranch.isElse) {
                                    this.ErrorWithParsedObject("Expected an '- else:' clause here rather than an extra condition", elseBranch);
                                    elseBranch.isElse = true;
                                }
                                alternatives.push(elseBranch);
                            }
                        }
                    }
                    // Still null?
                    if (alternatives === null) {
                        return null;
                    }
                }
                else if (alternatives.length === 1 &&
                    alternatives[0].isElse &&
                    initialQueryExpression) {
                    // Empty true branch - didn't get parsed, but should insert one for semantic correctness,
                    // and to make sure that any evaluation stack values get tidied up correctly.
                    const emptyTrueBranch = new ConditionalSingleBranch_1.ConditionalSingleBranch(null);
                    emptyTrueBranch.isTrueBranch = true;
                    alternatives.unshift(emptyTrueBranch);
                }
                // Like a switch statement
                // { initialQueryExpression:
                //    ... match the expression
                // }
                if (initialQueryExpression) {
                    let earlierBranchesHaveOwnExpression = false;
                    for (let ii = 0; ii < alternatives.length; ++ii) {
                        const branch = alternatives[ii];
                        const isLast = ii === alternatives.length - 1;
                        // Matching equality with initial query expression
                        // We set this flag even for the "else" clause so that
                        // it knows to tidy up the evaluation stack at the end
                        // Match query
                        if (branch.ownExpression) {
                            branch.matchingEquality = true;
                            earlierBranchesHaveOwnExpression = true;
                        }
                        else if (earlierBranchesHaveOwnExpression && isLast) {
                            // Else (final branch)
                            branch.matchingEquality = true;
                            branch.isElse = true;
                        }
                        else {
                            // Binary condition:
                            // { trueOrFalse:
                            //    - when true
                            //    - when false
                            // }
                            if (!isLast && alternatives.length > 2) {
                                this.ErrorWithParsedObject("Only final branch can be an 'else'. Did you miss a ':'?", branch);
                            }
                            else {
                                if (ii === 0) {
                                    branch.isTrueBranch = true;
                                }
                                else {
                                    branch.isElse = true;
                                }
                            }
                        }
                    }
                }
                else {
                    // No initial query, so just a multi-line conditional. e.g.:
                    // {
                    //   - x > 3:  greater than three
                    //   - x == 3: equal to three
                    //   - x < 3:  less than three
                    // }
                    for (let ii = 0; ii < alternatives.length; ++ii) {
                        const alt = alternatives[ii];
                        const isLast = ii === alternatives.length - 1;
                        if (alt.ownExpression === null) {
                            if (isLast) {
                                alt.isElse = true;
                            }
                            else {
                                if (alt.isElse) {
                                    // Do we ALSO have a valid "else" at the end? Let's report the error there.
                                    const finalClause = alternatives[alternatives.length - 1];
                                    if (finalClause.isElse) {
                                        this.ErrorWithParsedObject("Multiple 'else' cases. Can have a maximum of one, at the end.", finalClause);
                                    }
                                    else {
                                        this.ErrorWithParsedObject("'else' case in conditional should always be the final one", alt);
                                    }
                                }
                                else {
                                    this.ErrorWithParsedObject("Branch doesn't have condition. Are you missing a ':'? ", alt);
                                }
                            }
                        }
                    }
                    if (alternatives.length === 1 &&
                        alternatives[0].ownExpression === null) {
                        this.ErrorWithParsedObject("Condition block with no conditions", alternatives[0]);
                    }
                }
            }
            // TODO: Come up with water-tight error conditions... it's quite a flexible system!
            // e.g.
            //   - inline conditionals must have exactly 1 or 2 alternatives
            //   - multiline expression shouldn't have mixed existence of branch-conditions?
            if (alternatives === null) {
                return null;
            }
            for (const branch of alternatives) {
                branch.isInline = isInline;
            }
            const cond = new Conditional_1.Conditional(initialQueryExpression, alternatives);
            return cond;
        };
        this.InlineConditionalBranches = () => {
            const listOfLists = this.Interleave(this.MixedTextAndLogic, this.Exclude(this.String("|")), null, false);
            if (listOfLists === null || listOfLists.length === 0) {
                return null;
            }
            const result = [];
            if (listOfLists.length > 2) {
                this.Error("Expected one or two alternatives separated by '|' in inline conditional");
            }
            else {
                const trueBranch = new ConditionalSingleBranch_1.ConditionalSingleBranch(listOfLists[0]);
                trueBranch.isTrueBranch = true;
                result.push(trueBranch);
                if (listOfLists.length > 1) {
                    const elseBranch = new ConditionalSingleBranch_1.ConditionalSingleBranch(listOfLists[1]);
                    elseBranch.isElse = true;
                    result.push(elseBranch);
                }
            }
            return result;
        };
        this.MultilineConditionalBranches = () => {
            this.MultilineWhitespace();
            const multipleConditions = this.OneOrMore(this.SingleMultilineCondition);
            if (multipleConditions === null) {
                return null;
            }
            this.MultilineWhitespace();
            return multipleConditions;
        };
        this.SingleMultilineCondition = () => {
            this.Whitespace();
            if (
            // Make sure we're not accidentally parsing a divert
            this.ParseString("->") !== null ||
                this.ParseString("-") === null) {
                return null;
            }
            this.Whitespace();
            let expr = null;
            const isElse = this.Parse(this.ElseExpression) !== null;
            if (!isElse) {
                expr = this.Parse(this.ConditionExpression);
            }
            let content = this.StatementsAtLevel(StatementLevel_1.StatementLevel.InnerBlock);
            if (expr === null && content === null) {
                this.Error("expected content for the conditional branch following '-'");
                // Recover
                content = [new Text_1.Text("")];
            }
            // Allow additional multiline whitespace, if the statements were empty (valid)
            // then their surrounding multiline whitespacce needs to be handled manually.
            // e.g.
            // { x:
            //   - 1:    // intentionally left blank, but newline needs to be parsed
            //   - 2: etc
            // }
            this.MultilineWhitespace();
            const branch = new ConditionalSingleBranch_1.ConditionalSingleBranch(content);
            branch.ownExpression = expr;
            branch.isElse = isElse;
            return branch;
        };
        this.ConditionExpression = () => {
            const expr = this.Parse(this.Expression);
            if (expr === null) {
                return null;
            }
            this.DisallowIncrement(expr);
            this.Whitespace();
            if (this.ParseString(":") === null) {
                return null;
            }
            return expr;
        };
        this.ElseExpression = () => {
            if (this.ParseString("else") === null) {
                return null;
            }
            this.Whitespace();
            if (this.ParseString(":") === null) {
                return null;
            }
            return StringParser_1.ParseSuccess;
        };
        /**
         * End Conditional section.
         */
        /**
         * Begin Content section.
         */
        this._nonTextPauseCharacters = null;
        this._nonTextEndCharacters = null;
        this._notTextEndCharactersChoice = null;
        this._notTextEndCharactersString = null;
        this.TrimEndWhitespace = (mixedTextAndLogicResults, terminateWithSpace) => {
            // Trim whitespace from end
            if (mixedTextAndLogicResults.length > 0) {
                const lastObjIdx = mixedTextAndLogicResults.length - 1;
                const lastObj = mixedTextAndLogicResults[lastObjIdx];
                if (lastObj instanceof Text_1.Text) {
                    const textObj = lastObj;
                    textObj.text = textObj.text.replace(new RegExp(/[ \t]+$/g), "");
                    if (terminateWithSpace) {
                        textObj.text += " ";
                    }
                    else if (textObj.text.length === 0) {
                        // No content left at all? trim the whole object
                        mixedTextAndLogicResults.splice(lastObjIdx, 1);
                        // Recurse in case there's more whitespace
                        this.TrimEndWhitespace(mixedTextAndLogicResults, false);
                    }
                }
            }
        };
        this.LineOfMixedTextAndLogic = () => {
            // Consume any whitespace at the start of the line
            // (Except for escaped whitespace)
            this.Parse(this.Whitespace);
            let result = this.Parse(this.MixedTextAndLogic);
            if (!result || !result.length) {
                return null;
            }
            // Warn about accidentally writing "return" without "~"
            const firstText = result[0];
            if (firstText && firstText.text && firstText.text.startsWith("return")) {
                this.Warning("Do you need a '~' before 'return'? If not, perhaps use a glue: <> (since it's lowercase) or rewrite somehow?");
            }
            if (result.length === 0) {
                return null;
            }
            const lastObj = result[result.length - 1];
            if (!(lastObj instanceof Divert_1.Divert)) {
                this.TrimEndWhitespace(result, false);
            }
            this.EndTagIfNecessary(result);
            // If the line doens't actually contain any normal text content
            // but is in fact entirely a tag, then let's not append
            // a newline, since we want the tag (or tags) to be associated
            // with the line below rather than being completely independent.
            let lineIsPureTag = result.length > 0 && result[0] instanceof Tag_1.Tag && result[0].isStart;
            if (!lineIsPureTag) {
                result.push(new Text_1.Text("\n"));
            }
            this.Expect(this.EndOfLine, "end of line", this.SkipToNextLine);
            return result;
        };
        this.MixedTextAndLogic = () => {
            // Check for disallowed "~" within this context
            const disallowedTilde = this.ParseObject(this.Spaced(this.String("~")));
            if (disallowedTilde !== null) {
                this.Error("You shouldn't use a '~' here - tildas are for logic that's on its own line. To do inline logic, use { curly braces } instead");
            }
            // Either, or both interleaved
            let results = this.Interleave(this.Optional(this.ContentText), this.Optional(this.InlineLogicOrGlueOrStartTag));
            // Terminating divert?
            // (When parsing content for the text of a choice, diverts aren't allowed.
            //  The divert on the end of the body of a choice is handled specially.)
            if (!this._parsingChoice) {
                const diverts = this.Parse(this.MultiDivert);
                if (diverts !== null) {
                    // May not have had any results at all if there's *only* a divert!
                    if (results === null) {
                        results = [];
                    }
                    // End previously active tag if necessary
                    this.EndTagIfNecessary(results);
                    this.TrimEndWhitespace(results, true);
                    results.push(...diverts);
                }
            }
            if (!results) {
                return null;
            }
            return results;
        };
        this.ContentText = () => {
            return this.ContentTextAllowingEscapeChar();
        };
        this.ContentTextAllowingEscapeChar = () => {
            let sb = null;
            do {
                let str = this.Parse(this.ContentTextNoEscape);
                const gotEscapeChar = this.ParseString("\\") !== null;
                if (gotEscapeChar || str !== null) {
                    if (sb === null) {
                        sb = "";
                    }
                    if (str !== null) {
                        sb += String(str);
                    }
                    if (gotEscapeChar) {
                        const c = this.ParseSingleCharacter();
                        sb += c;
                    }
                }
                else {
                    break;
                }
            } while (true);
            if (sb !== null) {
                return new Text_1.Text(sb);
            }
            return null;
        };
        // Content text is an unusual parse rule compared with most since it's
        // less about saying "this is is the small selection of stuff that we parse"
        // and more "we parse ANYTHING except this small selection of stuff".
        this.ContentTextNoEscape = () => {
            // Eat through text, pausing at the following characters, and
            // attempt to parse the nonTextRule.
            // "-": possible start of divert or start of gather
            // "<": possible start of glue
            if (this._nonTextPauseCharacters === null) {
                this._nonTextPauseCharacters = new CharacterSet_1.CharacterSet("-<");
            }
            // If we hit any of these characters, we stop *immediately* without bothering to even check the nonTextRule
            // "{" for start of logic
            // "|" for mid logic branch
            if (this._nonTextEndCharacters === null) {
                this._nonTextEndCharacters = new CharacterSet_1.CharacterSet("{}|\n\r\\#");
                this._notTextEndCharactersChoice = new CharacterSet_1.CharacterSet(this._nonTextEndCharacters);
                this._notTextEndCharactersChoice.AddCharacters("[]");
                this._notTextEndCharactersString = new CharacterSet_1.CharacterSet(this._nonTextEndCharacters);
                this._notTextEndCharactersString.AddCharacters('"');
            }
            // When the ParseUntil pauses, check these rules in case they evaluate successfully
            const nonTextRule = () => this.OneOf([
                this.ParseDivertArrow,
                this.ParseThreadArrow,
                this.EndOfLine,
                this.Glue,
            ]);
            let endChars = null;
            if (this.parsingStringExpression) {
                endChars = this._notTextEndCharactersString;
            }
            else if (this._parsingChoice) {
                endChars = this._notTextEndCharactersChoice;
            }
            else {
                endChars = this._nonTextEndCharacters;
            }
            const pureTextContent = this.ParseUntil(nonTextRule, this._nonTextPauseCharacters, endChars);
            if (pureTextContent !== null) {
                return pureTextContent;
            }
            return null;
        };
        /**
         * End Content section.
         */
        /**
         * Begin Divert section.
         */
        this.MultiDivert = () => {
            this.Whitespace();
            let diverts = [];
            // Try single thread first
            const threadDivert = this.Parse(this.StartThread);
            if (threadDivert) {
                diverts = [threadDivert];
                return diverts;
            }
            // Normal diverts and tunnels
            const arrowsAndDiverts = this.Interleave(this.ParseDivertArrowOrTunnelOnwards, this.DivertIdentifierWithArguments);
            if (!arrowsAndDiverts) {
                return null;
            }
            diverts = [];
            this.EndTagIfNecessary(diverts);
            // Possible patterns:
            //  ->                   -- explicit gather
            //  ->->                 -- tunnel onwards
            //  -> div               -- normal divert
            //  ->-> div             -- tunnel onwards, followed by override divert
            //  -> div ->            -- normal tunnel
            //  -> div ->->          -- tunnel then tunnel continue
            //  -> div -> div        -- tunnel then divert
            //  -> div -> div ->     -- tunnel then tunnel
            //  -> div -> div ->->
            //  -> div -> div ->-> div    (etc)
            // Look at the arrows and diverts
            for (let ii = 0; ii < arrowsAndDiverts.length; ++ii) {
                const isArrow = ii % 2 === 0;
                // Arrow string
                if (isArrow) {
                    // Tunnel onwards
                    if (arrowsAndDiverts[ii] === "->->") {
                        const tunnelOnwardsPlacementValid = ii === 0 ||
                            ii === arrowsAndDiverts.length - 1 ||
                            ii === arrowsAndDiverts.length - 2;
                        if (!tunnelOnwardsPlacementValid) {
                            this.Error("Tunnel onwards '->->' must only come at the begining or the start of a divert");
                        }
                        const tunnelOnwards = new TunnelOnwards_1.TunnelOnwards();
                        if (ii < arrowsAndDiverts.length - 1) {
                            const tunnelOnwardDivert = (0, TypeAssertion_1.asOrNull)(arrowsAndDiverts[ii + 1], Divert_1.Divert);
                            tunnelOnwards.divertAfter = tunnelOnwardDivert;
                        }
                        diverts.push(tunnelOnwards);
                        // Not allowed to do anything after a tunnel onwards.
                        // If we had anything left it would be caused in the above Error for
                        // the positioning of a ->->
                        break;
                    }
                }
                else {
                    // Divert
                    const divert = arrowsAndDiverts[ii];
                    // More to come? (further arrows) Must be tunnelling.
                    if (ii < arrowsAndDiverts.length - 1) {
                        divert.isTunnel = true;
                    }
                    diverts.push(divert);
                }
            }
            // Single -> (used for default choices)
            if (diverts.length === 0 && arrowsAndDiverts.length === 1) {
                const gatherDivert = new Divert_1.Divert(null);
                gatherDivert.isEmpty = true;
                diverts.push(gatherDivert);
                if (!this._parsingChoice) {
                    this.Error("Empty diverts (->) are only valid on choices");
                }
            }
            return diverts;
        };
        this.StartThread = () => {
            this.Whitespace();
            if (this.ParseThreadArrow() === null) {
                return null;
            }
            this.Whitespace();
            const divert = this.Expect(this.DivertIdentifierWithArguments, "target for new thread", () => new Divert_1.Divert(null));
            divert.isThread = true;
            return divert;
        };
        this.DivertIdentifierWithArguments = () => {
            this.Whitespace();
            const targetComponents = this.Parse(this.DotSeparatedDivertPathComponents);
            if (!targetComponents) {
                return null;
            }
            this.Whitespace();
            const optionalArguments = this.Parse(this.ExpressionFunctionCallArguments);
            this.Whitespace();
            const targetPath = new Path_1.Path(targetComponents);
            return new Divert_1.Divert(targetPath, optionalArguments);
        };
        this.SingleDivert = () => {
            const diverts = this.Parse(this.MultiDivert);
            if (!diverts) {
                return null;
            }
            // Ideally we'd report errors if we get the
            // wrong kind of divert, but unfortunately we
            // have to hack around the fact that sequences use
            // a very similar syntax.
            // i.e. if you have a multi-divert at the start
            // of a sequence, it initially tries to parse it
            // as a divert target (part of an expression of
            // a conditional) and gives errors. So instead
            // we just have to blindly reject it as a single
            // divert, and give a slightly less nice error
            // when you DO use a multi divert as a divert taret.
            if (diverts.length !== 1) {
                return null;
            }
            const singleDivert = diverts[0];
            if (singleDivert instanceof TunnelOnwards_1.TunnelOnwards) {
                return null;
            }
            const divert = diverts[0];
            if (divert.isTunnel) {
                return null;
            }
            return divert;
        };
        this.DotSeparatedDivertPathComponents = () => this.Interleave(this.Spaced(this.IdentifierWithMetadata), this.Exclude(this.String(".")));
        this.ParseDivertArrowOrTunnelOnwards = () => {
            let numArrows = 0;
            while (this.ParseString("->") !== null) {
                numArrows += 1;
            }
            if (numArrows === 0) {
                return null;
            }
            else if (numArrows === 1) {
                return "->";
            }
            else if (numArrows === 2) {
                return "->->";
            }
            this.Error("Unexpected number of arrows in divert. Should only have '->' or '->->'");
            return "->->";
        };
        this.ParseDivertArrow = () => this.ParseString("->");
        this.ParseThreadArrow = () => this.ParseString("<-");
        /**
         * End Divert section.
         */
        /**
         * Begin Expressions section.
         */
        this._binaryOperators = [];
        this._maxBinaryOpLength = 0;
        this.TempDeclarationOrAssignment = () => {
            this.Whitespace();
            const isNewDeclaration = this.ParseTempKeyword();
            this.Whitespace();
            let varIdentifier = null;
            if (isNewDeclaration) {
                varIdentifier = this.Expect(this.IdentifierWithMetadata, "variable name");
            }
            else {
                varIdentifier = this.Parse(this.IdentifierWithMetadata);
            }
            if (varIdentifier === null) {
                return null;
            }
            this.Whitespace();
            // += -=
            const isIncrement = this.ParseString("+") !== null;
            const isDecrement = this.ParseString("-") !== null;
            if (isIncrement && isDecrement) {
                this.Error("Unexpected sequence '+-'");
            }
            if (this.ParseString("=") === null) {
                // Definitely in an assignment expression?
                if (isNewDeclaration) {
                    this.Error("Expected '='");
                }
                return null;
            }
            const assignedExpression = this.Expect(this.Expression, "value expression to be assigned");
            if (isIncrement || isDecrement) {
                const result = new IncDecExpression_1.IncDecExpression(varIdentifier, assignedExpression, isIncrement);
                return result;
            }
            const result = new VariableAssignment_1.VariableAssignment({
                variableIdentifier: varIdentifier,
                assignedExpression,
                isTemporaryNewDeclaration: isNewDeclaration,
            });
            return result;
        };
        this.DisallowIncrement = (expr) => {
            if (expr instanceof IncDecExpression_1.IncDecExpression) {
                this.Error("Can't use increment/decrement here. It can only be used on a ~ line");
            }
        };
        this.ParseTempKeyword = () => {
            const ruleId = this.BeginRule();
            if (this.Parse(this.Identifier) === "temp") {
                this.SucceedRule(ruleId);
                return true;
            }
            this.FailRule(ruleId);
            return false;
        };
        this.ReturnStatement = () => {
            this.Whitespace();
            const returnOrDone = this.Parse(this.Identifier);
            if (returnOrDone !== "return") {
                return null;
            }
            this.Whitespace();
            const expr = this.Parse(this.Expression);
            const returnObj = new ReturnType_1.ReturnType(expr);
            return returnObj;
        };
        // Pratt Parser
        // aka "Top down operator precedence parser"
        // http://journal.stuffwithstuff.com/2011/03/19/pratt-parsers-expression-parsing-made-easy/
        // Algorithm overview:
        // The two types of precedence are handled in two different ways:
        //   ((((a . b) . c) . d) . e)			#1
        //   (a . (b . (c . (d . e))))			#2
        // Where #1 is automatically handled by successive loops within the main 'while' in this function,
        // so long as continuing operators have lower (or equal) precedence (e.g. imagine some series of "*"s then "+" above.
        // ...and #2 is handled by recursion of the right hand term in the binary expression parser.
        // (see link for advice on how to extend for postfix and mixfix operators)
        this.Expression = (minimumPrecedence = 0) => {
            this.Whitespace();
            // First parse a unary expression e.g. "-a" or parethensised "(1 + 2)"
            let expr = this.ExpressionUnary();
            if (expr === null) {
                return null;
            }
            this.Whitespace();
            // Attempt to parse (possibly multiple) continuing infix expressions (e.g. 1 + 2 + 3)
            while (true) {
                const ruleId = this.BeginRule();
                // Operator
                const infixOp = this.ParseInfixOperator();
                if (infixOp !== null && infixOp.precedence > minimumPrecedence) {
                    // Expect right hand side of operator
                    const expectationMessage = `right side of '${infixOp.type}' expression`;
                    const multiaryExpr = this.Expect(() => this.ExpressionInfixRight(expr, infixOp), expectationMessage);
                    if (multiaryExpr === null) {
                        // Fail for operator and right-hand side of multiary expression
                        this.FailRule(ruleId);
                        return null;
                    }
                    expr = this.SucceedRule(ruleId, multiaryExpr);
                    continue;
                }
                this.FailRule(ruleId);
                break;
            }
            this.Whitespace();
            return expr;
        };
        this.ExpressionUnary = () => {
            // Divert target is a special case - it can't have any other operators
            // applied to it, and we also want to check for it first so that we don't
            // confuse "->" for subtraction.
            const divertTarget = this.Parse(this.ExpressionDivertTarget);
            if (divertTarget !== null) {
                return divertTarget;
            }
            let prefixOp = this.OneOf([
                this.String("-"),
                this.String("!"),
            ]);
            // Don't parse like the string rules above, in case its actually
            // a variable that simply starts with "not", e.g. "notable".
            // This rule uses the Identifier rule, which will scan as much text
            // as possible before returning.
            if (prefixOp === null) {
                prefixOp = this.Parse(this.ExpressionNot);
            }
            this.Whitespace();
            // - Since we allow numbers at the start of variable names, variable names are checked before literals
            // - Function calls before variable names in case we see parentheses
            let expr = this.OneOf([
                this.ExpressionList,
                this.ExpressionParen,
                this.ExpressionFunctionCall,
                this.ExpressionVariableName,
                this.ExpressionLiteral,
            ]);
            // Only recurse immediately if we have one of the (usually optional) unary ops
            if (expr === null && prefixOp !== null) {
                expr = this.ExpressionUnary();
            }
            if (expr === null) {
                return null;
            }
            else if (prefixOp !== null) {
                expr = UnaryExpression_1.UnaryExpression.WithInner(expr, prefixOp);
            }
            this.Whitespace();
            const postfixOp = this.OneOf([this.String("++"), this.String("--")]);
            if (postfixOp !== null) {
                const isInc = postfixOp === "++";
                if (!(expr instanceof VariableReference_1.VariableReference)) {
                    this.Error(`can only increment and decrement variables, but saw '${expr}'.`);
                    // Drop down and succeed without the increment after reporting error
                }
                else {
                    const varRef = expr;
                    expr = new IncDecExpression_1.IncDecExpression(varRef.identifier, isInc);
                }
            }
            return expr;
        };
        this.ExpressionNot = () => {
            const id = this.Identifier();
            if (id === "not") {
                return id;
            }
            return null;
        };
        this.ExpressionLiteral = () => this.OneOf([
            this.ExpressionFloat,
            this.ExpressionInt,
            this.ExpressionBool,
            this.ExpressionString,
        ]);
        this.ExpressionDivertTarget = () => {
            this.Whitespace();
            const divert = this.Parse(this.SingleDivert);
            if (!divert || (divert && divert.isThread)) {
                return null;
            }
            this.Whitespace();
            return new DivertTarget_1.DivertTarget(divert);
        };
        this.ExpressionInt = () => {
            const intOrNull = this.ParseInt();
            if (intOrNull === null) {
                return null;
            }
            return new NumberExpression_1.NumberExpression(intOrNull, "int");
        };
        this.ExpressionFloat = () => {
            const floatOrNull = this.ParseFloat();
            if (floatOrNull === null) {
                return null;
            }
            return new NumberExpression_1.NumberExpression(floatOrNull, "float");
        };
        this.ExpressionString = () => {
            const openQuote = this.ParseString('"');
            if (openQuote === null) {
                return null;
            }
            // Set custom parser state flag so that within the text parser,
            // it knows to treat the quote character (") as an end character
            this.parsingStringExpression = true;
            let textAndLogic = this.Parse(this.MixedTextAndLogic);
            this.Expect(this.String('"'), "close quote for string expression");
            this.parsingStringExpression = false;
            if (textAndLogic === null) {
                textAndLogic = [new Text_1.Text("")];
            }
            else if (textAndLogic.find((c) => c instanceof Divert_1.Divert)) {
                this.Error("String expressions cannot contain diverts (->)");
            }
            return new StringExpression_1.StringExpression(textAndLogic);
        };
        this.ExpressionBool = () => {
            const id = this.Parse(this.Identifier);
            if (id === "true") {
                return new NumberExpression_1.NumberExpression(true, "bool");
            }
            else if (id === "false") {
                return new NumberExpression_1.NumberExpression(false, "bool");
            }
            return null;
        };
        this.ExpressionFunctionCall = () => {
            const iden = this.Parse(this.IdentifierWithMetadata);
            if (iden === null) {
                return null;
            }
            this.Whitespace();
            const args = this.Parse(this.ExpressionFunctionCallArguments);
            if (args === null) {
                return null;
            }
            return new FunctionCall_1.FunctionCall(iden, args);
        };
        this.ExpressionFunctionCallArguments = () => {
            if (this.ParseString("(") === null) {
                return null;
            }
            // "Exclude" requires the rule to succeed, but causes actual comma string to be excluded from the list of results
            const commas = this.Exclude(this.String(","));
            let args = this.Interleave(this.Expression, commas);
            if (args === null) {
                args = [];
            }
            this.Whitespace();
            this.Expect(this.String(")"), "closing ')' for function call");
            return args;
        };
        this.ExpressionVariableName = () => {
            const path = this.Interleave(this.IdentifierWithMetadata, this.Exclude(this.Spaced(this.String("."))));
            if (path === null || Story_1.Story.IsReservedKeyword(path[0].name)) {
                return null;
            }
            return new VariableReference_1.VariableReference(path);
        };
        this.ExpressionParen = () => {
            if (this.ParseString("(") === null) {
                return null;
            }
            const innerExpr = this.Parse(this.Expression);
            if (innerExpr === null) {
                return null;
            }
            this.Whitespace();
            this.Expect(this.String(")"), "closing parenthesis ')' for expression");
            return innerExpr;
        };
        this.ExpressionInfixRight = (left, op) => {
            if (!left) {
                return null;
            }
            this.Whitespace();
            const right = this.Parse(() => this.Expression(op.precedence));
            if (right) {
                // We assume that the character we use for the operator's type is the same
                // as that used internally by e.g. Runtime.Expression.Add, Runtime.Expression.Multiply etc
                const expr = new BinaryExpression_1.BinaryExpression(left, right, op.type);
                return expr;
            }
            return null;
        };
        this.ParseInfixOperator = () => {
            for (const op of this._binaryOperators) {
                const ruleId = this.BeginRule();
                if (this.ParseString(op.type) !== null) {
                    if (op.requireWhitespace) {
                        if (this.Whitespace() === null) {
                            this.FailRule(ruleId);
                            continue;
                        }
                    }
                    return this.SucceedRule(ruleId, op);
                }
                this.FailRule(ruleId);
            }
            return null;
        };
        this.ExpressionList = () => {
            this.Whitespace();
            if (this.ParseString("(") === null) {
                return null;
            }
            this.Whitespace();
            // When list has:
            //  - 0 elements (null list) - this is okay, it's an empty list: "()"
            //  - 1 element - it could be confused for a single non-list related
            //    identifier expression in brackets, but this is a useless thing
            //    to do, so we reserve that syntax for a list with one item.
            //  - 2 or more elements - normal!
            const memberNames = this.SeparatedList(this.ListMember, this.Spaced(this.String(",")));
            this.Whitespace();
            // May have failed to parse the inner list - the parentheses may
            // be for a normal expression
            if (this.ParseString(")") === null) {
                return null;
            }
            return new List_1.List(memberNames);
        };
        this.ListMember = () => {
            this.Whitespace();
            let identifier = this.Parse(this.IdentifierWithMetadata);
            if (identifier === null) {
                return null;
            }
            const dot = this.ParseString(".");
            if (dot !== null) {
                const identifier2 = this.Expect(this.IdentifierWithMetadata, `element name within the set ${identifier}`);
                identifier.name += `.${identifier2 === null || identifier2 === void 0 ? void 0 : identifier2.name}`;
            }
            this.Whitespace();
            return identifier;
        };
        this.RegisterExpressionOperators = () => {
            // These will be tried in order, so we need "<=" before "<"
            // for correctness
            this.RegisterBinaryOperator("&&", 1);
            this.RegisterBinaryOperator("||", 1);
            this.RegisterBinaryOperator("and", 1, true);
            this.RegisterBinaryOperator("or", 1, true);
            this.RegisterBinaryOperator("==", 2);
            this.RegisterBinaryOperator(">=", 2);
            this.RegisterBinaryOperator("<=", 2);
            this.RegisterBinaryOperator("<", 2);
            this.RegisterBinaryOperator(">", 2);
            this.RegisterBinaryOperator("!=", 2);
            // (apples, oranges) + cabbages has (oranges, cabbages) === true
            this.RegisterBinaryOperator("?", 3);
            this.RegisterBinaryOperator("has", 3, true);
            this.RegisterBinaryOperator("!?", 3);
            this.RegisterBinaryOperator("hasnt", 3, true);
            this.RegisterBinaryOperator("^", 3);
            this.RegisterBinaryOperator("+", 4);
            this.RegisterBinaryOperator("-", 5);
            this.RegisterBinaryOperator("*", 6);
            this.RegisterBinaryOperator("/", 7);
            this.RegisterBinaryOperator("%", 8);
            this.RegisterBinaryOperator("mod", 8, true);
        };
        this.RegisterBinaryOperator = (op, precedence, requireWhitespace = false) => {
            const infix = new InfixOperator_1.InfixOperator(op, precedence, requireWhitespace);
            this._binaryOperators.push(infix);
            this._maxBinaryOpLength = Math.max(this._maxBinaryOpLength, op.length);
        };
        this._openFilenames = [];
        this.IncludeStatement = () => {
            this.Whitespace();
            if (this.ParseString("INCLUDE") === null) {
                return null;
            }
            this.Whitespace();
            let filename = this.Expect(() => this.ParseUntilCharactersFromString("\n\r"), "filename for include statement");
            filename = filename.replace(new RegExp(/[ \t]+$/g), "");
            // Working directory should already have been set up relative to the root ink file.
            const fullFilename = this.fileHandler.ResolveInkFilename(filename, this._filename);
            if (this.FilenameIsAlreadyOpen(fullFilename)) {
                this.Error(`Recursive INCLUDE detected: '${fullFilename}' is already open.`);
                this.ParseUntilCharactersFromString("\r\n");
                return new IncludedFile_1.IncludedFile(null);
            }
            else {
                this.AddOpenFilename(fullFilename);
            }
            let includedStory = null;
            let includedString = "";
            try {
                includedString = this._rootParser.fileHandler.LoadInkFileContents(fullFilename, this._filename);
            }
            catch (err) {
                this.Error(`Failed to load: '${filename}'.\nError:${err}`);
            }
            if (includedString != null) {
                const parser = new InkParser(includedString, filename, this._externalErrorHandler, this._rootParser, this.fileHandler);
                includedStory = parser.ParseStory();
            }
            this.RemoveOpenFilename(fullFilename);
            // Return valid IncludedFile object even if there were errors when parsing.
            // We don't want to attempt to re-parse the include line as something else,
            // and we want to include the bits that *are* valid, so we don't generate
            // more errors than necessary.
            return new IncludedFile_1.IncludedFile(includedStory);
        };
        this.FilenameIsAlreadyOpen = (fullFilename) => this._rootParser._openFilenames.includes(fullFilename);
        this.AddOpenFilename = (fullFilename) => {
            this._rootParser._openFilenames.push(fullFilename);
        };
        this.RemoveOpenFilename = (fullFilename) => {
            this._rootParser._openFilenames.splice(this._rootParser._openFilenames.indexOf(fullFilename), 1);
        };
        /**
         * End Include section.
         */
        /**
         * Begin Knot section.
         */
        this.KnotDefinition = () => {
            const knotDecl = this.Parse(this.KnotDeclaration);
            if (knotDecl === null) {
                return null;
            }
            this.Expect(this.EndOfLine, "end of line after knot name definition", this.SkipToNextLine);
            const innerKnotStatements = () => this.StatementsAtLevel(StatementLevel_1.StatementLevel.Knot);
            const content = this.Expect(innerKnotStatements, "at least one line within the knot", this.KnotStitchNoContentRecoveryRule);
            return new Knot_1.Knot(knotDecl.name, content, knotDecl.args, knotDecl.isFunction);
        };
        this.KnotDeclaration = () => {
            this.Whitespace();
            if (this.KnotTitleEquals() === null) {
                return null;
            }
            this.Whitespace();
            const identifier = this.Parse(this.IdentifierWithMetadata);
            let knotName;
            const isFunc = (identifier === null || identifier === void 0 ? void 0 : identifier.name) === "function";
            if (isFunc) {
                this.Expect(this.Whitespace, "whitespace after the 'function' keyword");
                knotName = this.Parse(this.IdentifierWithMetadata);
            }
            else {
                knotName = identifier;
            }
            if (knotName === null) {
                this.Error(`Expected the name of the ${isFunc ? "function" : "knot"}`);
                knotName = new Identifier_1.Identifier(""); // prevent later null ref
            }
            this.Whitespace();
            const parameterNames = this.Parse(this.BracketedKnotDeclArguments);
            this.Whitespace();
            // Optional equals after name
            this.Parse(this.KnotTitleEquals);
            return new FlowDecl_1.FlowDecl(knotName, parameterNames, isFunc);
        };
        this.KnotTitleEquals = () => {
            // 2+ "=" starts a knot
            const multiEquals = this.ParseCharactersFromString("=");
            if (multiEquals === null || multiEquals.length <= 1) {
                return null;
            }
            return multiEquals;
        };
        this.StitchDefinition = () => {
            const decl = this.Parse(this.StitchDeclaration);
            if (decl === null) {
                return null;
            }
            this.Expect(this.EndOfLine, "end of line after stitch name", this.SkipToNextLine);
            const innerStitchStatements = () => this.StatementsAtLevel(StatementLevel_1.StatementLevel.Stitch);
            const content = this.Expect(innerStitchStatements, "at least one line within the stitch", this.KnotStitchNoContentRecoveryRule);
            return new Stitch_1.Stitch(decl.name, content, decl.args, decl.isFunction);
        };
        this.StitchDeclaration = () => {
            this.Whitespace();
            // Single "=" to define a stitch
            if (this.ParseString("=") === null) {
                return null;
            }
            // If there's more than one "=", that's actually a knot definition (or divert), so this rule should fail
            if (this.ParseString("=") !== null) {
                return null;
            }
            this.Whitespace();
            // Stitches aren't allowed to be functions, but we parse it anyway and report the error later
            const isFunc = this.ParseString("function") !== null;
            if (isFunc) {
                this.Whitespace();
            }
            const stitchName = this.Parse(this.IdentifierWithMetadata);
            if (stitchName === null) {
                return null;
            }
            this.Whitespace();
            const flowArgs = this.Parse(this.BracketedKnotDeclArguments);
            this.Whitespace();
            return new FlowDecl_1.FlowDecl(stitchName, flowArgs, isFunc);
        };
        this.KnotStitchNoContentRecoveryRule = () => {
            // Jump ahead to the next knot or the end of the file
            this.ParseUntil(this.KnotDeclaration, new CharacterSet_1.CharacterSet("="), null);
            const recoveredFlowContent = [new Text_1.Text("<ERROR IN FLOW>")];
            return recoveredFlowContent;
        };
        this.BracketedKnotDeclArguments = () => {
            if (this.ParseString("(") === null) {
                return null;
            }
            let flowArguments = this.Interleave(this.Spaced(this.FlowDeclArgument), this.Exclude(this.String(",")));
            this.Expect(this.String(")"), "closing ')' for parameter list");
            // If no parameters, create an empty list so that this method is type safe and
            // doesn't attempt to return the ParseSuccess object
            if (flowArguments === null) {
                flowArguments = [];
            }
            return flowArguments;
        };
        this.FlowDeclArgument = () => {
            // Possible forms:
            //  name
            //  -> name      (variable divert target argument
            //  ref name
            //  ref -> name  (variable divert target by reference)
            const firstIden = this.Parse(this.IdentifierWithMetadata);
            this.Whitespace();
            const divertArrow = this.ParseDivertArrow();
            this.Whitespace();
            const secondIden = this.Parse(this.IdentifierWithMetadata);
            if (firstIden == null && secondIden === null) {
                return null;
            }
            const flowArg = new Argument_1.Argument();
            if (divertArrow !== null) {
                flowArg.isDivertTarget = true;
            }
            // Passing by reference
            if (firstIden !== null && firstIden.name === "ref") {
                if (secondIden === null) {
                    this.Error("Expected an parameter name after 'ref'");
                }
                flowArg.identifier = secondIden;
                flowArg.isByReference = true;
            }
            else {
                // Simple argument name
                if (flowArg.isDivertTarget) {
                    flowArg.identifier = secondIden;
                }
                else {
                    flowArg.identifier = firstIden;
                }
                if (flowArg.identifier === null) {
                    this.Error("Expected an parameter name");
                }
                flowArg.isByReference = false;
            }
            return flowArg;
        };
        this.ExternalDeclaration = () => {
            this.Whitespace();
            const external = this.Parse(this.IdentifierWithMetadata);
            if (external === null || external.name != "EXTERNAL") {
                return null;
            }
            this.Whitespace();
            const funcIdentifier = this.Expect(this.IdentifierWithMetadata, "name of external function") || new Identifier_1.Identifier("");
            this.Whitespace();
            let parameterNames = this.Expect(this.BracketedKnotDeclArguments, `declaration of arguments for EXTERNAL, even if empty, i.e. 'EXTERNAL ${funcIdentifier}()'`);
            if (parameterNames === null) {
                parameterNames = [];
            }
            const argNames = parameterNames
                .map((arg) => { var _a; return (_a = arg.identifier) === null || _a === void 0 ? void 0 : _a.name; })
                .filter(TypeAssertion_1.filterUndef);
            return new ExternalDeclaration_1.ExternalDeclaration(funcIdentifier, argNames);
        };
        /**
         * End Knot section.
         */
        /**
         * Start Logic section.
         */
        this._identifierCharSet = null;
        this.LogicLine = () => {
            this.Whitespace();
            if (this.ParseString("~") === null) {
                return null;
            }
            this.Whitespace();
            // Some example lines we need to be able to distinguish between:
            // ~ temp x = 5  -- var decl + assign
            // ~ temp x      -- var decl
            // ~ x = 5       -- var assign
            // ~ x           -- expr (not var decl or assign)
            // ~ f()         -- expr
            // We don't treat variable decl/assign as an expression since we don't want an assignment
            // to have a return value, or to be used in compound expressions.
            const afterTilde = () => this.OneOf([
                this.ReturnStatement,
                this.TempDeclarationOrAssignment,
                this.Expression,
            ]);
            let result = this.Expect(afterTilde, "expression after '~'", this.SkipToNextLine);
            // Prevent further errors, already reported expected expression and have skipped to next line.
            if (result === null) {
                return new ContentList_1.ContentList();
            }
            // Parse all expressions, but tell the writer off if they did something useless like:
            //  ~ 5 + 4
            // And even:
            //  ~ false && myFunction()
            // ...since it's bad practice, and won't do what they expect if
            // they're expecting C's lazy evaluation.
            if (result instanceof Expression_1.Expression &&
                !(result instanceof FunctionCall_1.FunctionCall || result instanceof IncDecExpression_1.IncDecExpression)) {
                this.Error("Logic following a '~' can't be that type of expression. It can only be something like:\n\t~ return\n\t~ var x = blah\n\t~ x++\n\t~ myFunction()");
            }
            // Line is pure function call? e.g.
            //  ~ f()
            // Add extra pop to make sure we tidy up after ourselves.
            // We no longer need anything on the evaluation stack.
            const funCall = (0, TypeAssertion_1.asOrNull)(result, FunctionCall_1.FunctionCall);
            if (funCall) {
                funCall.shouldPopReturnedValue = true;
            }
            // If the expression contains a function call, then it could produce a text side effect,
            // in which case it needs a newline on the end. e.g.
            //  ~ printMyName()
            //  ~ x = 1 + returnAValueAndAlsoPrintStuff()
            // If no text gets printed, then the extra newline will have to be culled later.
            // Multiple newlines on the output will be removed, so there will be no "leak" for
            // long running calculations. It's disappointingly messy though :-/
            if (result.Find(FunctionCall_1.FunctionCall)() !== null) {
                result = new ContentList_1.ContentList(result, new Text_1.Text("\n"));
            }
            this.Expect(this.EndOfLine, "end of line", this.SkipToNextLine);
            return result;
        };
        this.VariableDeclaration = () => {
            this.Whitespace();
            const id = this.Parse(this.Identifier);
            if (id !== "VAR") {
                return null;
            }
            this.Whitespace();
            const varName = this.Expect(this.IdentifierWithMetadata, "variable name");
            this.Whitespace();
            this.Expect(this.String("="), "the '=' for an assignment of a value, e.g. '= 5' (initial values are mandatory)");
            this.Whitespace();
            const definition = this.Expect(this.Expression, "initial value for ");
            const expr = definition;
            if (expr) {
                const check = expr instanceof NumberExpression_1.NumberExpression ||
                    expr instanceof StringExpression_1.StringExpression ||
                    expr instanceof DivertTarget_1.DivertTarget ||
                    expr instanceof VariableReference_1.VariableReference ||
                    expr instanceof List_1.List;
                if (!check) {
                    this.Error("initial value for a variable must be a number, constant, list or divert target");
                }
                if (this.Parse(this.ListElementDefinitionSeparator) !== null) {
                    this.Error("Unexpected ','. If you're trying to declare a new list, use the LIST keyword, not VAR");
                }
                else if (expr instanceof StringExpression_1.StringExpression) {
                    // Ensure string expressions are simple
                    const strExpr = expr;
                    if (!strExpr.isSingleString) {
                        this.Error("Constant strings cannot contain any logic.");
                    }
                }
                const result = new VariableAssignment_1.VariableAssignment({
                    assignedExpression: expr,
                    isGlobalDeclaration: true,
                    variableIdentifier: varName,
                });
                return result;
            }
            return null;
        };
        this.ListDeclaration = () => {
            this.Whitespace();
            const id = this.Parse(this.Identifier);
            if (id != "LIST") {
                return null;
            }
            this.Whitespace();
            const varName = this.Expect(this.IdentifierWithMetadata, "list name");
            this.Whitespace();
            this.Expect(this.String("="), "the '=' for an assignment of the list definition");
            this.Whitespace();
            const definition = this.Expect(this.ListDefinition, "list item names");
            if (definition) {
                definition.identifier = new Identifier_1.Identifier(varName.name);
                return new VariableAssignment_1.VariableAssignment({
                    variableIdentifier: varName,
                    listDef: definition,
                });
            }
            return null;
        };
        this.ListDefinition = () => {
            this.AnyWhitespace();
            const allElements = this.SeparatedList(this.ListElementDefinition, this.ListElementDefinitionSeparator);
            if (allElements === null) {
                return null;
            }
            return new ListDefinition_1.ListDefinition(allElements);
        };
        this.ListElementDefinitionSeparator = () => {
            this.AnyWhitespace();
            if (this.ParseString(",") === null) {
                return null;
            }
            this.AnyWhitespace();
            return ",";
        };
        this.ListElementDefinition = () => {
            const inInitialList = this.ParseString("(") !== null;
            let needsToCloseParen = inInitialList;
            this.Whitespace();
            const name = this.Parse(this.IdentifierWithMetadata);
            if (name === null) {
                return null;
            }
            this.Whitespace();
            if (inInitialList) {
                if (this.ParseString(")") != null) {
                    needsToCloseParen = false;
                    this.Whitespace();
                }
            }
            let elementValue = null;
            if (this.ParseString("=") !== null) {
                this.Whitespace();
                const elementValueNum = this.Expect(this.ExpressionInt, "value to be assigned to list item");
                if (elementValueNum !== null) {
                    elementValue = elementValueNum.value;
                }
                if (needsToCloseParen) {
                    this.Whitespace();
                    if (this.ParseString(")") !== null) {
                        needsToCloseParen = false;
                    }
                }
            }
            if (needsToCloseParen) {
                this.Error("Expected closing ')'");
            }
            return new ListElementDefinition_1.ListElementDefinition(name, inInitialList, elementValue);
        };
        this.ConstDeclaration = () => {
            this.Whitespace();
            const id = this.Parse(this.Identifier);
            if (id !== "CONST") {
                return null;
            }
            this.Whitespace();
            const varName = this.Expect(this.IdentifierWithMetadata, "constant name");
            this.Whitespace();
            this.Expect(this.String("="), "the '=' for an assignment of a value, e.g. '= 5' (initial values are mandatory)");
            this.Whitespace();
            const expr = this.Expect(this.Expression, "initial value for ");
            const check = expr instanceof NumberExpression_1.NumberExpression ||
                expr instanceof DivertTarget_1.DivertTarget ||
                expr instanceof StringExpression_1.StringExpression;
            if (!check) {
                this.Error("initial value for a constant must be a number or divert target");
            }
            else if (expr instanceof StringExpression_1.StringExpression) {
                // Ensure string expressions are simple
                const strExpr = expr;
                if (!strExpr.isSingleString) {
                    this.Error("Constant strings cannot contain any logic.");
                }
            }
            const result = new ConstantDeclaration_1.ConstantDeclaration(varName, expr);
            return result;
        };
        this.InlineLogicOrGlueOrStartTag = () => this.OneOf([this.InlineLogic, this.Glue, this.StartTag]);
        this.Glue = () => {
            // Don't want to parse whitespace, since it might be important
            // surrounding the glue.
            const glueStr = this.ParseString("<>");
            if (glueStr !== null) {
                return new Glue_1.Glue(new Glue_2.Glue());
            }
            return null;
        };
        this.InlineLogic = () => {
            if (this.ParseString("{") === null) {
                return null;
            }
            let wasParsingString = this.parsingStringExpression;
            let wasTagActive = this.tagActive;
            this.Whitespace();
            const logic = this.Expect(this.InnerLogic, "some kind of logic, conditional or sequence within braces: { ... }");
            if (logic === null) {
                this.parsingStringExpression = wasParsingString;
                return null;
            }
            this.DisallowIncrement(logic);
            let contentList = (0, TypeAssertion_1.asOrNull)(logic, ContentList_1.ContentList);
            if (!contentList) {
                contentList = new ContentList_1.ContentList(logic);
            }
            this.Whitespace();
            this.Expect(this.String("}"), "closing brace '}' for inline logic");
            // Allow nested strings and logic
            this.parsingStringExpression = wasParsingString;
            // Difference between:
            //
            //     1) A thing # {image}.jpg
            //     2) A {red #red|blue #blue} sequence.
            //
            //  When logic ends in (1) we still want tag to continue.
            //  When logic ends in (2) we want to auto-end the tag.
            //  Side note: we simply disallow tags within strings.
            if (!wasTagActive)
                this.EndTagIfNecessary(contentList);
            return contentList;
        };
        this.InnerLogic = () => {
            this.Whitespace();
            // Explicitly try the combinations of inner logic
            // that could potentially have conflicts first.
            // Explicit sequence annotation?
            const explicitSeqType = this.ParseObject(this.SequenceTypeAnnotation);
            if (explicitSeqType !== null) {
                const contentLists = this.Expect(this.InnerSequenceObjects, "sequence elements (for cycle/stoping etc)");
                if (contentLists === null) {
                    return null;
                }
                return new Sequence_1.Sequence(contentLists, explicitSeqType);
            }
            // Conditional with expression?
            const initialQueryExpression = this.Parse(this.ConditionExpression);
            if (initialQueryExpression) {
                const conditional = this.Expect(() => this.InnerConditionalContent(initialQueryExpression), "conditional content following query");
                return conditional;
            }
            // Now try to evaluate each of the "full" rules in turn
            const rules = [
                // Conditional still necessary, since you can have a multi-line conditional
                // without an initial query expression:
                // {
                //   - true:  this is true
                //   - false: this is false
                // }
                this.InnerConditionalContent,
                this.InnerSequence,
                this.InnerExpression,
            ];
            //let wasTagActiveAtStartOfScope = this.tagActive;
            // Adapted from "OneOf" structuring rule except that in
            // order for the rule to succeed, it has to maximally
            // cover the entire string within the { }. Used to
            // differentiate between:
            //  {myVar}                 -- Expression (try first)
            //  {my content is jolly}   -- sequence with single element
            for (const rule of rules) {
                const ruleId = this.BeginRule();
                const result = this.ParseObject(rule);
                if (result) {
                    // Not yet at end?
                    if (this.Peek(this.Spaced(this.String("}"))) === null) {
                        this.FailRule(ruleId);
                    }
                    else {
                        // Full parse of content within braces
                        return this.SucceedRule(ruleId, result);
                    }
                }
                else {
                    this.FailRule(ruleId);
                }
            }
            return null;
        };
        this.InnerExpression = () => {
            const expr = this.Parse(this.Expression);
            if (expr) {
                expr.outputWhenComplete = true;
            }
            return expr;
        };
        this.IdentifierWithMetadata = () => {
            const id = this.Identifier();
            if (id === null) {
                return null;
            }
            return new Identifier_1.Identifier(id);
        };
        // Note: we allow identifiers that start with a number,
        // but not if they *only* comprise numbers
        this.Identifier = () => {
            // Parse remaining characters (if any)
            const name = this.ParseCharactersFromCharSet(this.identifierCharSet);
            if (name === null) {
                return null;
            }
            // Reject if it's just a number
            let isNumberCharsOnly = true;
            for (let c of name) {
                if (!(c >= "0" && c <= "9")) {
                    isNumberCharsOnly = false;
                    break;
                }
            }
            if (isNumberCharsOnly) {
                return null;
            }
            return name;
        };
        /**
         * End Logic section.
         */
        /**
         * Begin Sequences section.
         */
        this._sequenceTypeSymbols = new CharacterSet_1.CharacterSet("!&~$");
        this.InnerSequence = () => {
            this.Whitespace();
            // Default sequence type
            let seqType = SequenceType_1.SequenceType.Stopping;
            // Optional explicit sequence type
            const parsedSeqType = this.Parse(this.SequenceTypeAnnotation);
            if (parsedSeqType !== null) {
                seqType = parsedSeqType;
            }
            const contentLists = this.Parse(this.InnerSequenceObjects);
            if (contentLists === null || contentLists.length <= 1) {
                return null;
            }
            return new Sequence_1.Sequence(contentLists, seqType);
        };
        this.SequenceTypeAnnotation = () => {
            let annotation = this.Parse(this.SequenceTypeSymbolAnnotation);
            if (annotation === null) {
                annotation = this.Parse(this.SequenceTypeWordAnnotation);
            }
            if (annotation === null) {
                return null;
            }
            switch (annotation) {
                case SequenceType_1.SequenceType.Once:
                case SequenceType_1.SequenceType.Cycle:
                case SequenceType_1.SequenceType.Stopping:
                case SequenceType_1.SequenceType.Shuffle:
                // eslint-disable-next-line @typescript-eslint/no-unsafe-enum-comparison
                case SequenceType_1.SequenceType.Shuffle | SequenceType_1.SequenceType.Stopping:
                // eslint-disable-next-line @typescript-eslint/no-unsafe-enum-comparison
                case SequenceType_1.SequenceType.Shuffle | SequenceType_1.SequenceType.Once:
                    break;
                default:
                    this.Error(`Sequence type combination not supported: ${annotation}`);
                    return SequenceType_1.SequenceType.Stopping;
            }
            return annotation;
        };
        this.SequenceTypeSymbolAnnotation = () => {
            if (this._sequenceTypeSymbols === null) {
                this._sequenceTypeSymbols = new CharacterSet_1.CharacterSet("!&~$ ");
            }
            let sequenceType = 0;
            const sequenceAnnotations = this.ParseCharactersFromCharSet(this._sequenceTypeSymbols);
            if (sequenceAnnotations === null) {
                return null;
            }
            for (const symbolChar of sequenceAnnotations) {
                switch (symbolChar) {
                    case "!":
                        sequenceType |= SequenceType_1.SequenceType.Once;
                        break;
                    case "&":
                        sequenceType |= SequenceType_1.SequenceType.Cycle;
                        break;
                    case "~":
                        sequenceType |= SequenceType_1.SequenceType.Shuffle;
                        break;
                    case "$":
                        sequenceType |= SequenceType_1.SequenceType.Stopping;
                        break;
                }
            }
            if (sequenceType === 0) {
                return null;
            }
            return sequenceType;
        };
        this.SequenceTypeWordAnnotation = () => {
            const sequenceTypes = this.Interleave(this.SequenceTypeSingleWord, this.Exclude(this.Whitespace));
            if (sequenceTypes === null || sequenceTypes.length === 0) {
                return null;
            }
            if (this.ParseString(":") === null) {
                return null;
            }
            let combinedSequenceType = 0;
            for (const seqType of sequenceTypes) {
                combinedSequenceType |= seqType;
            }
            return combinedSequenceType;
        };
        this.SequenceTypeSingleWord = () => {
            let seqType = null;
            const word = this.Parse(this.IdentifierWithMetadata);
            if (word !== null) {
                switch (word.name) {
                    case "once":
                        seqType = SequenceType_1.SequenceType.Once;
                        break;
                    case "cycle":
                        seqType = SequenceType_1.SequenceType.Cycle;
                        break;
                    case "shuffle":
                        seqType = SequenceType_1.SequenceType.Shuffle;
                        break;
                    case "stopping":
                        seqType = SequenceType_1.SequenceType.Stopping;
                        break;
                }
            }
            if (seqType === null) {
                return null;
            }
            return seqType;
        };
        this.InnerSequenceObjects = () => {
            const multiline = this.Parse(this.Newline) !== null;
            let result = null;
            if (multiline) {
                result = this.Parse(this.InnerMultilineSequenceObjects);
            }
            else {
                result = this.Parse(this.InnerInlineSequenceObjects);
            }
            return result;
        };
        this.InnerInlineSequenceObjects = () => {
            const interleavedContentAndPipes = this.Interleave(this.Optional(this.MixedTextAndLogic), this.String("|"), null, false);
            if (interleavedContentAndPipes === null) {
                return null;
            }
            const result = [];
            // The content and pipes won't necessarily be perfectly interleaved in the sense that
            // the content can be missing, but in that case it's intended that there's blank content.
            let justHadContent = false;
            for (const contentOrPipe of interleavedContentAndPipes) {
                // Pipe/separator
                if (contentOrPipe === "|") {
                    // Expected content, saw pipe - need blank content now
                    if (!justHadContent) {
                        // Add blank content
                        result.push(new ContentList_1.ContentList());
                    }
                    justHadContent = false;
                }
                else {
                    // Real content
                    const content = contentOrPipe;
                    if (content === null) {
                        this.Error(`Expected content, but got ${contentOrPipe} (this is an ink compiler bug!)`);
                    }
                    else {
                        result.push(new ContentList_1.ContentList(content));
                    }
                    justHadContent = true;
                }
            }
            // Ended in a pipe? Need to insert final blank content
            if (!justHadContent) {
                result.push(new ContentList_1.ContentList());
            }
            return result;
        };
        this.InnerMultilineSequenceObjects = () => {
            this.MultilineWhitespace();
            const contentLists = this.OneOrMore(this.SingleMultilineSequenceElement);
            if (contentLists === null) {
                return null;
            }
            return contentLists;
        };
        this.SingleMultilineSequenceElement = () => {
            this.Whitespace();
            // Make sure we're not accidentally parsing a divert
            if (this.ParseString("->") !== null) {
                return null;
            }
            if (this.ParseString("-") === null) {
                return null;
            }
            this.Whitespace();
            const content = this.StatementsAtLevel(StatementLevel_1.StatementLevel.InnerBlock);
            if (content === null) {
                this.MultilineWhitespace();
            }
            else {
                // Add newline at the start of each branch
                content.unshift(new Text_1.Text("\n"));
            }
            return new ContentList_1.ContentList(content);
        };
        /**
         * End Sequences section.
         */
        /**
         * Begin Statements section.
         */
        this._statementRulesAtLevel = [];
        this._statementBreakRulesAtLevel = [];
        this.StatementsAtLevel = (level) => {
            // Check for error: Should not be allowed gather dashes within an inner block
            if (level === StatementLevel_1.StatementLevel.InnerBlock) {
                const badGatherDashCount = this.Parse(this.GatherDashes);
                if (badGatherDashCount !== null) {
                    this.Error("You can't use a gather (the dashes) within the { curly braces } context. For multi-line sequences and conditions, you should only use one dash.");
                }
            }
            return this.Interleave(this.Optional(this.MultilineWhitespace), () => this.StatementAtLevel(level), () => this.StatementsBreakForLevel(level));
        };
        this.StatementAtLevel = (level) => {
            const rulesAtLevel = this._statementRulesAtLevel[level];
            const statement = this.OneOf(rulesAtLevel);
            // For some statements, allow them to parse, but create errors, since
            // writers may think they can use the statement, so it's useful to have
            // the error message.
            if (level === StatementLevel_1.StatementLevel.Top) {
                if (statement instanceof ReturnType_1.ReturnType) {
                    this.Error("should not have return statement outside of a knot");
                }
            }
            return statement;
        };
        this.StatementsBreakForLevel = (level) => {
            this.Whitespace();
            const breakRules = this._statementBreakRulesAtLevel[level];
            const breakRuleResult = this.OneOf(breakRules);
            if (breakRuleResult === null) {
                return null;
            }
            return breakRuleResult;
        };
        this.GenerateStatementLevelRules = () => {
            const levels = Object.values(StatementLevel_1.StatementLevel);
            this._statementRulesAtLevel = "f"
                .repeat(levels.length)
                .split("f")
                .map(() => []);
            this._statementBreakRulesAtLevel = "f"
                .repeat(levels.length)
                .split("f")
                .map(() => []);
            for (const level of levels) {
                const rulesAtLevel = [];
                const breakingRules = [];
                // Diverts can go anywhere
                rulesAtLevel.push(this.Line(this.MultiDivert));
                // Knots can only be parsed at Top/Global scope
                if (level >= StatementLevel_1.StatementLevel.Top) {
                    rulesAtLevel.push(this.KnotDefinition);
                }
                rulesAtLevel.push(this.Line(this.Choice));
                rulesAtLevel.push(this.Line(this.AuthorWarning));
                // Gather lines would be confused with multi-line block separators, like
                // within a multi-line if statement
                if (level > StatementLevel_1.StatementLevel.InnerBlock) {
                    rulesAtLevel.push(this.Gather);
                }
                // Stitches (and gathers) can (currently) only go in Knots and top level
                if (level >= StatementLevel_1.StatementLevel.Knot) {
                    rulesAtLevel.push(this.StitchDefinition);
                }
                // Global variable declarations can go anywhere
                rulesAtLevel.push(this.Line(this.ListDeclaration));
                rulesAtLevel.push(this.Line(this.VariableDeclaration));
                rulesAtLevel.push(this.Line(this.ConstDeclaration));
                rulesAtLevel.push(this.Line(this.ExternalDeclaration));
                // Global include can go anywhere
                rulesAtLevel.push(this.Line(this.IncludeStatement));
                // Normal logic / text can go anywhere
                rulesAtLevel.push(this.LogicLine);
                rulesAtLevel.push(this.LineOfMixedTextAndLogic);
                // --------
                // Breaking rules
                // Break current knot with a new knot
                if (level <= StatementLevel_1.StatementLevel.Knot) {
                    breakingRules.push(this.KnotDeclaration);
                }
                // Break current stitch with a new stitch
                if (level <= StatementLevel_1.StatementLevel.Stitch) {
                    breakingRules.push(this.StitchDeclaration);
                }
                // Breaking an inner block (like a multi-line condition statement)
                if (level <= StatementLevel_1.StatementLevel.InnerBlock) {
                    breakingRules.push(this.ParseDashNotArrow);
                    breakingRules.push(this.String("}"));
                }
                this._statementRulesAtLevel[level] = rulesAtLevel;
                this._statementBreakRulesAtLevel[level] = breakingRules;
            }
        };
        this.SkipToNextLine = () => {
            this.ParseUntilCharactersFromString("\n\r");
            this.ParseNewline();
            return StringParser_1.ParseSuccess;
        };
        // Modifier to turn a rule into one that expects a newline on the end.
        // e.g. anywhere you can use "MixedTextAndLogic" as a rule, you can use
        // "Line(MixedTextAndLogic)" to specify that it expects a newline afterwards.
        this.Line = (inlineRule) => () => {
            const result = this.ParseObject(inlineRule);
            if (result === null) {
                return null;
            }
            this.Expect(this.EndOfLine, "end of line", this.SkipToNextLine);
            return result;
        };
        /**
         * End Statements section.
         */
        /**
         * Begin Tags section.
         */
        this.StartTag = () => {
            this.Whitespace();
            if (this.ParseString("#") === null) {
                return null;
            }
            if (this.parsingStringExpression) {
                this.Error("Tags aren't allowed inside of strings. Please use \\# if you want a hash symbol.");
            }
            let result = null;
            if (this.tagActive) {
                let contentList = new ContentList_1.ContentList();
                contentList.AddContent(new Tag_1.Tag(/*isStart:*/ false));
                contentList.AddContent(new Tag_1.Tag(/*isStart:*/ true));
                result = contentList;
            }
            else {
                result = new Tag_1.Tag(/*isStart:*/ true);
            }
            this.tagActive = true;
            this.Whitespace();
            return result;
        };
        /**
         * End Tags section.
         */
        /**
         * Begin Whitespace section.
         */
        this._inlineWhitespaceChars = new CharacterSet_1.CharacterSet(" \t");
        // Handles both newline and endOfFile
        this.EndOfLine = () => this.OneOf([this.Newline, this.EndOfFile]);
        // Allow whitespace before the actual newline
        this.Newline = () => {
            this.Whitespace();
            const gotNewline = this.ParseNewline() !== null;
            // Optional \r, definite \n to support Windows (\r\n) and Mac/Unix (\n)
            if (!gotNewline) {
                return null;
            }
            return StringParser_1.ParseSuccess;
        };
        this.EndOfFile = () => {
            this.Whitespace();
            if (!this.endOfInput)
                return null;
            return StringParser_1.ParseSuccess;
        };
        // General purpose space, returns N-count newlines (fails if no newlines)
        this.MultilineWhitespace = () => {
            let newlines = this.OneOrMore(this.Newline);
            if (newlines === null) {
                return null;
            }
            // Use content field of Token to say how many newlines there were
            // (in most circumstances it's unimportant)
            const numNewlines = newlines.length;
            if (numNewlines >= 1) {
                return StringParser_1.ParseSuccess;
            }
            return null;
        };
        this.Whitespace = () => {
            const doneParsed = this.ParseCharactersFromCharSet(this._inlineWhitespaceChars);
            if (doneParsed !== null) {
                return StringParser_1.ParseSuccess;
            }
            return null;
        };
        this.Spaced = (rule) => () => {
            this.Whitespace();
            const result = this.ParseObject(rule);
            if (result === null) {
                return null;
            }
            this.Whitespace();
            return result;
        };
        this.AnyWhitespace = () => {
            let anyWhitespace = false;
            while (this.OneOf([this.Whitespace, this.MultilineWhitespace]) !== null) {
                anyWhitespace = true;
            }
            return anyWhitespace ? StringParser_1.ParseSuccess : null;
        };
        this.MultiSpaced = (rule) => () => {
            this.AnyWhitespace();
            const result = this.ParseObject(rule);
            if (result === null) {
                return null;
            }
            this.AnyWhitespace();
            return result;
        };
        this._filename = null;
        this._externalErrorHandler = null;
        this._fileHandler = null;
        this._filename = filename;
        this.RegisterExpressionOperators();
        this.GenerateStatementLevelRules();
        this.errorHandler = this.OnStringParserError;
        this._externalErrorHandler = externalErrorHandler;
        if (fileHandler === null) {
            this._fileHandler = new DefaultFileHandler_1.DefaultFileHandler();
        }
        else {
            this._fileHandler = fileHandler;
        }
        if (rootParser === null) {
            this._rootParser = this;
            this._openFilenames = [];
            if (this._filename !== null) {
                const fullRootInkPath = this.fileHandler.ResolveInkFilename(this._filename);
                this._openFilenames.push(fullRootInkPath);
            }
        }
        else {
            this._rootParser = rootParser;
        }
    }
    PreProcessInputString(str) {
        const commentEliminator = new CommentEliminator_1.CommentEliminator(str);
        return commentEliminator.Process();
    }
    get parsingStringExpression() {
        return this.GetFlag(Number(CustomFlags_1.CustomFlags.ParsingString));
    }
    set parsingStringExpression(value) {
        this.SetFlag(Number(CustomFlags_1.CustomFlags.ParsingString), value);
    }
    get tagActive() {
        return this.GetFlag(Number(CustomFlags_1.CustomFlags.TagActive));
    }
    set tagActive(value) {
        this.SetFlag(Number(CustomFlags_1.CustomFlags.TagActive), value);
    }
    get identifierCharSet() {
        if (this._identifierCharSet === null) {
            (this._identifierCharSet = new CharacterSet_1.CharacterSet())
                .AddRange("A", "Z")
                .AddRange("a", "z")
                .AddRange("0", "9")
                .Add("_");
            // Enable non-ASCII characters for story identifiers.
            this.ExtendIdentifierCharacterRanges(this._identifierCharSet);
        }
        return this._identifierCharSet;
    }
    EndTagIfNecessary(outputContentList) {
        if (this.tagActive) {
            if (outputContentList != null) {
                if (outputContentList instanceof ContentList_1.ContentList) {
                    outputContentList.AddContent(new Tag_1.Tag(/*isStart:*/ false));
                }
                else {
                    outputContentList.push(new Tag_1.Tag(/*isStart:*/ false));
                }
            }
            this.tagActive = false;
        }
    }
}
exports.InkParser = InkParser;
/**
 * End base InkParser section.
 */
/**
 * Begin CharacterRanges section.
 */
InkParser.LatinBasic = CharacterRange_1.CharacterRange.Define("\u0041", "\u007A", new CharacterSet_1.CharacterSet().AddRange("\u005B", "\u0060"));
InkParser.LatinExtendedA = CharacterRange_1.CharacterRange.Define("\u0100", "\u017F"
// no excludes here
);
InkParser.LatinExtendedB = CharacterRange_1.CharacterRange.Define("\u0180", "\u024F"
// no excludes here
);
InkParser.Greek = CharacterRange_1.CharacterRange.Define("\u0370", "\u03FF", new CharacterSet_1.CharacterSet()
    .AddRange("\u0378", "\u0385")
    .AddCharacters("\u0374\u0375\u0378\u0387\u038B\u038D\u03A2"));
InkParser.Cyrillic = CharacterRange_1.CharacterRange.Define("\u0400", "\u04FF", new CharacterSet_1.CharacterSet().AddRange("\u0482", "\u0489"));
InkParser.Armenian = CharacterRange_1.CharacterRange.Define("\u0530", "\u058F", new CharacterSet_1.CharacterSet()
    .AddCharacters("\u0530")
    .AddRange("\u0557", "\u0560")
    .AddRange("\u0588", "\u058E"));
InkParser.Hebrew = CharacterRange_1.CharacterRange.Define("\u0590", "\u05FF", new CharacterSet_1.CharacterSet());
InkParser.Arabic = CharacterRange_1.CharacterRange.Define("\u0600", "\u06FF", new CharacterSet_1.CharacterSet());
InkParser.Korean = CharacterRange_1.CharacterRange.Define("\uAC00", "\uD7AF", new CharacterSet_1.CharacterSet());
InkParser.Latin1Supplement = CharacterRange_1.CharacterRange.Define("\u0080", "\u00FF", new CharacterSet_1.CharacterSet());
InkParser.Chinese = CharacterRange_1.CharacterRange.Define("\u4E00", "\u9FFF", new CharacterSet_1.CharacterSet());
/// <summary>
/// Gets an array of <see cref="CharacterRange" /> representing all of the currently supported
/// non-ASCII character ranges that can be used in identifier names.
/// </summary>
/// <returns>
/// An array of <see cref="CharacterRange" /> representing all of the currently supported
/// non-ASCII character ranges that can be used in identifier names.
/// </returns>
InkParser.ListAllCharacterRanges = () => [
    InkParser.LatinBasic,
    InkParser.LatinExtendedA,
    InkParser.LatinExtendedB,
    InkParser.Arabic,
    InkParser.Armenian,
    InkParser.Cyrillic,
    InkParser.Greek,
    InkParser.Hebrew,
    InkParser.Korean,
    InkParser.Latin1Supplement,
    InkParser.Chinese,
];
//# sourceMappingURL=InkParser.js.map