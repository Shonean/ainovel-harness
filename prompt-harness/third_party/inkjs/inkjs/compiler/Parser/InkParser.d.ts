import { Argument } from "./ParsedHierarchy/Argument";
import { AuthorWarning } from "./ParsedHierarchy/AuthorWarning";
import { BinaryExpression } from "./ParsedHierarchy/Expression/BinaryExpression";
import { CharacterRange } from "./CharacterRange";
import { CharacterSet } from "./CharacterSet";
import { Choice } from "./ParsedHierarchy/Choice";
import { Conditional } from "./ParsedHierarchy/Conditional/Conditional";
import { ConditionalSingleBranch } from "./ParsedHierarchy/Conditional/ConditionalSingleBranch";
import { ContentList } from "./ParsedHierarchy/ContentList";
import { DebugMetadata } from "../../engine/DebugMetadata";
import { Divert } from "./ParsedHierarchy/Divert/Divert";
import { Expression } from "./ParsedHierarchy/Expression/Expression";
import { ErrorHandler } from "../../engine/Error";
import { ExternalDeclaration } from "./ParsedHierarchy/Declaration/ExternalDeclaration";
import { FlowDecl } from "./FlowDecl";
import { Gather } from "./ParsedHierarchy/Gather/Gather";
import { Glue } from "./ParsedHierarchy/Glue";
import { IFileHandler } from "../IFileHandler";
import { IncludedFile } from "./ParsedHierarchy/IncludedFile";
import { InfixOperator } from "./InfixOperator";
import { Knot } from "./ParsedHierarchy/Knot";
import { List } from "./ParsedHierarchy/List/List";
import { ListDefinition } from "./ParsedHierarchy/List/ListDefinition";
import { ListElementDefinition } from "./ParsedHierarchy/List/ListElementDefinition";
import { ParsedObject } from "./ParsedHierarchy/Object";
import { ReturnType } from "./ParsedHierarchy/ReturnType";
import { Sequence } from "./ParsedHierarchy/Sequence/Sequence";
import { SequenceType } from "./ParsedHierarchy/Sequence/SequenceType";
import { StatementLevel } from "./StatementLevel";
import { Story } from "./ParsedHierarchy/Story";
import { StringExpression } from "./ParsedHierarchy/Expression/StringExpression";
import { StringParser, SpecificParseRule, ParseRule, ParseRuleReturn, ParseSuccess } from "./StringParser/StringParser";
import { StringParserElement } from "./StringParser/StringParserElement";
import { Text } from "./ParsedHierarchy/Text";
import { VariableAssignment } from "./ParsedHierarchy/Variable/VariableAssignment";
import { Identifier } from "./ParsedHierarchy/Identifier";
import { NumberExpression } from "./ParsedHierarchy/Expression/NumberExpression";
export declare class InkParser extends StringParser {
    /**
     * Begin base InkParser section.
     */
    get fileHandler(): IFileHandler;
    set fileHandler(value: IFileHandler);
    constructor(str: string, filename?: string | null, externalErrorHandler?: ErrorHandler | null, rootParser?: InkParser | null, fileHandler?: IFileHandler | null);
    readonly ParseStory: () => Story;
    readonly SeparatedList: <T extends ParseRule>(mainRule: SpecificParseRule<T>, separatorRule: ParseRule) => ParseRuleReturn[] | null;
    PreProcessInputString(str: string): string;
    readonly CreateDebugMetadata: (stateAtStart: StringParserElement | null, stateAtEnd: StringParserElement) => DebugMetadata;
    readonly RuleDidSucceed: (result: ParseRuleReturn, stateAtStart: StringParserElement | null, stateAtEnd: StringParserElement) => void;
    get parsingStringExpression(): boolean;
    set parsingStringExpression(value: boolean);
    get tagActive(): boolean;
    set tagActive(value: boolean);
    readonly OnStringParserError: (message: string, index: number, lineIndex?: number, isWarning?: boolean) => void;
    readonly AuthorWarning: () => AuthorWarning | null;
    /**
     * End base InkParser section.
     */
    /**
     * Begin CharacterRanges section.
     */
    static readonly LatinBasic: CharacterRange;
    static readonly LatinExtendedA: CharacterRange;
    static readonly LatinExtendedB: CharacterRange;
    static readonly Greek: CharacterRange;
    static readonly Cyrillic: CharacterRange;
    static readonly Armenian: CharacterRange;
    static readonly Hebrew: CharacterRange;
    static readonly Arabic: CharacterRange;
    static readonly Korean: CharacterRange;
    static readonly Latin1Supplement: CharacterRange;
    static readonly Chinese: CharacterRange;
    private readonly ExtendIdentifierCharacterRanges;
    static readonly ListAllCharacterRanges: () => CharacterRange[];
    /**
     * End CharacterRanges section.
     */
    /**
     * Begin Choices section.
     */
    _parsingChoice: boolean;
    readonly Choice: () => Choice | null;
    readonly ChoiceCondition: () => Expression | null;
    readonly ChoiceConditionsSpace: () => typeof ParseSuccess;
    readonly ChoiceSingleCondition: () => Expression | null;
    readonly Gather: () => Gather | null;
    readonly GatherDashes: () => number | null;
    readonly ParseDashNotArrow: () => ParseRuleReturn;
    readonly BracketedName: () => Identifier | null;
    /**
     * End Choices section.
     */
    /**
     * Begin Conditional section.
     */
    readonly InnerConditionalContent: (initialQueryExpression: Expression) => Conditional | null;
    readonly InlineConditionalBranches: () => ConditionalSingleBranch[] | null;
    readonly MultilineConditionalBranches: () => ConditionalSingleBranch[] | null;
    readonly SingleMultilineCondition: () => ConditionalSingleBranch | null;
    readonly ConditionExpression: () => ParsedObject | null;
    readonly ElseExpression: () => typeof ParseSuccess | null;
    /**
     * End Conditional section.
     */
    /**
     * Begin Content section.
     */
    _nonTextPauseCharacters: CharacterSet | null;
    _nonTextEndCharacters: CharacterSet | null;
    _notTextEndCharactersChoice: CharacterSet | null;
    _notTextEndCharactersString: CharacterSet | null;
    readonly TrimEndWhitespace: (mixedTextAndLogicResults: ParsedObject[], terminateWithSpace: boolean) => void;
    readonly LineOfMixedTextAndLogic: () => ParsedObject[] | null;
    readonly MixedTextAndLogic: () => ParsedObject[] | null;
    readonly ContentText: () => Text | null;
    readonly ContentTextAllowingEscapeChar: () => Text | null;
    readonly ContentTextNoEscape: () => string | null;
    /**
     * End Content section.
     */
    /**
     * Begin Divert section.
     */
    readonly MultiDivert: () => ParsedObject[] | null;
    readonly StartThread: () => Divert | null;
    readonly DivertIdentifierWithArguments: () => Divert | null;
    readonly SingleDivert: () => Divert | null;
    readonly DotSeparatedDivertPathComponents: () => Identifier[];
    readonly ParseDivertArrowOrTunnelOnwards: () => string | null;
    readonly ParseDivertArrow: () => string | null;
    readonly ParseThreadArrow: () => string | null;
    /**
     * End Divert section.
     */
    /**
     * Begin Expressions section.
     */
    _binaryOperators: InfixOperator[];
    _maxBinaryOpLength: number;
    readonly TempDeclarationOrAssignment: () => ParsedObject | null;
    readonly DisallowIncrement: (expr: ParsedObject) => void;
    readonly ParseTempKeyword: () => boolean;
    readonly ReturnStatement: () => ReturnType | null;
    readonly Expression: (minimumPrecedence?: number) => Expression | null;
    readonly ExpressionUnary: () => Expression | null;
    readonly ExpressionNot: () => string | null;
    readonly ExpressionLiteral: () => Expression;
    readonly ExpressionDivertTarget: () => Expression | null;
    readonly ExpressionInt: () => NumberExpression | null;
    readonly ExpressionFloat: () => NumberExpression | null;
    readonly ExpressionString: () => StringExpression | null;
    readonly ExpressionBool: () => NumberExpression | null;
    readonly ExpressionFunctionCall: () => Expression | null;
    readonly ExpressionFunctionCallArguments: () => Expression[] | null;
    readonly ExpressionVariableName: () => Expression | null;
    readonly ExpressionParen: () => Expression | null;
    readonly ExpressionInfixRight: (left: Expression | null, op: InfixOperator) => BinaryExpression | null;
    private readonly ParseInfixOperator;
    readonly ExpressionList: () => List | null;
    readonly ListMember: () => Identifier | null;
    readonly RegisterExpressionOperators: () => void;
    readonly RegisterBinaryOperator: (op: string, precedence: number, requireWhitespace?: boolean) => void;
    /**
     * End Expressions section.
     */
    /**
     * Begin Include section.
     */
    private _rootParser;
    private _openFilenames;
    readonly IncludeStatement: () => IncludedFile | null;
    readonly FilenameIsAlreadyOpen: (fullFilename: string) => boolean;
    readonly AddOpenFilename: (fullFilename: string) => void;
    readonly RemoveOpenFilename: (fullFilename: string) => void;
    /**
     * End Include section.
     */
    /**
     * Begin Knot section.
     */
    readonly KnotDefinition: () => Knot | null;
    readonly KnotDeclaration: () => FlowDecl | null;
    readonly KnotTitleEquals: () => string | null;
    readonly StitchDefinition: () => ParseRuleReturn;
    readonly StitchDeclaration: () => FlowDecl | null;
    readonly KnotStitchNoContentRecoveryRule: () => ParseRuleReturn;
    readonly BracketedKnotDeclArguments: () => Argument[] | null;
    readonly FlowDeclArgument: () => Argument | null;
    readonly ExternalDeclaration: () => ExternalDeclaration | null;
    /**
     * End Knot section.
     */
    /**
     * Start Logic section.
     */
    private _identifierCharSet;
    get identifierCharSet(): CharacterSet;
    readonly LogicLine: () => ParsedObject | null;
    readonly VariableDeclaration: () => ParsedObject | null;
    readonly ListDeclaration: () => VariableAssignment | null;
    readonly ListDefinition: () => ListDefinition | null;
    readonly ListElementDefinitionSeparator: () => string | null;
    readonly ListElementDefinition: () => ListElementDefinition | null;
    readonly ConstDeclaration: () => ParsedObject | null;
    readonly InlineLogicOrGlueOrStartTag: () => ParsedObject;
    readonly Glue: () => Glue | null;
    readonly InlineLogic: () => ContentList | null;
    readonly InnerLogic: () => ParsedObject | null;
    readonly InnerExpression: () => ParsedObject;
    readonly IdentifierWithMetadata: () => Identifier | null;
    readonly Identifier: () => string | null;
    /**
     * End Logic section.
     */
    /**
     * Begin Sequences section.
     */
    _sequenceTypeSymbols: CharacterSet;
    readonly InnerSequence: () => Sequence | null;
    readonly SequenceTypeAnnotation: () => ParseRuleReturn;
    readonly SequenceTypeSymbolAnnotation: () => ParseRuleReturn;
    readonly SequenceTypeWordAnnotation: () => ParseRuleReturn;
    readonly SequenceTypeSingleWord: () => SequenceType | null;
    readonly InnerSequenceObjects: () => ContentList[];
    readonly InnerInlineSequenceObjects: () => ContentList[] | null;
    readonly InnerMultilineSequenceObjects: () => ContentList[] | null;
    readonly SingleMultilineSequenceElement: () => ContentList | null;
    /**
     * End Sequences section.
     */
    /**
     * Begin Statements section.
     */
    private _statementRulesAtLevel;
    private _statementBreakRulesAtLevel;
    readonly StatementsAtLevel: (level: StatementLevel) => ParsedObject[];
    readonly StatementAtLevel: (level: StatementLevel) => ParsedObject;
    readonly StatementsBreakForLevel: (level: StatementLevel) => ParseRuleReturn;
    readonly GenerateStatementLevelRules: () => void;
    readonly SkipToNextLine: () => typeof ParseSuccess;
    readonly Line: (inlineRule: ParseRule) => ParseRule;
    /**
     * End Statements section.
     */
    /**
     * Begin Tags section.
     */
    readonly StartTag: () => ParsedObject | null;
    EndTagIfNecessary(outputContentList: ParsedObject[] | null): void;
    EndTagIfNecessary(outputContentList: ContentList | null): void;
    /**
     * End Tags section.
     */
    /**
     * Begin Whitespace section.
     */
    private _inlineWhitespaceChars;
    readonly EndOfLine: () => ParseRuleReturn;
    readonly Newline: () => typeof ParseSuccess | null;
    readonly EndOfFile: () => typeof ParseSuccess | null;
    readonly MultilineWhitespace: () => typeof ParseSuccess | null;
    readonly Whitespace: () => typeof ParseSuccess | null;
    readonly Spaced: (rule: ParseRule) => ParseRule;
    readonly AnyWhitespace: () => typeof ParseSuccess | null;
    readonly MultiSpaced: (rule: ParseRule) => ParseRuleReturn;
    private _filename;
    private _externalErrorHandler;
    private _fileHandler;
}
