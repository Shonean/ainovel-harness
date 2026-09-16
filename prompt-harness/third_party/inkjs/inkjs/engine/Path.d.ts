export declare class Path {
    static parentId: string;
    _isRelative: boolean;
    _components: Path.Component[];
    _componentsString: string | null;
    constructor();
    constructor(componentsString: string);
    constructor(head: Path.Component, tail: Path);
    constructor(head: Path.Component[], relative?: boolean);
    get isRelative(): boolean;
    get componentCount(): number;
    get head(): Path.Component | null;
    get tail(): Path;
    get length(): number;
    get lastComponent(): Path.Component | null;
    get containsNamedComponent(): boolean;
    static get self(): Path;
    GetComponent(index: number): Path.Component;
    PathByAppendingPath(pathToAppend: Path): Path;
    get componentsString(): string;
    set componentsString(value: string);
    toString(): string;
    Equals(otherPath: Path | null): boolean;
    PathByAppendingComponent(c: Path.Component): Path;
}
export declare namespace Path {
    class Component {
        readonly index: number;
        readonly name: string | null;
        constructor(indexOrName: string | number);
        get isIndex(): boolean;
        get isParent(): boolean;
        static ToParent(): Component;
        toString(): string | null;
        Equals(otherComp: Component): boolean;
    }
}
