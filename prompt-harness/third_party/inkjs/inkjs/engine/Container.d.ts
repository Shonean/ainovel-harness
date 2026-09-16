import { StringBuilder } from "./StringBuilder";
import { INamedContent } from "./INamedContent";
import { InkObject } from "./Object";
import { SearchResult } from "./SearchResult";
import { Path } from "./Path";
export declare class Container extends InkObject implements INamedContent {
    name: string | null;
    _content: InkObject[];
    namedContent: Map<string, INamedContent>;
    visitsShouldBeCounted: boolean;
    turnIndexShouldBeCounted: boolean;
    countingAtStartOnly: boolean;
    _pathToFirstLeafContent: Path | null;
    get hasValidName(): boolean;
    get content(): InkObject[];
    set content(value: InkObject[]);
    get namedOnlyContent(): Map<string, InkObject> | null;
    set namedOnlyContent(value: Map<string, InkObject> | null);
    get countFlags(): number;
    set countFlags(value: number);
    get pathToFirstLeafContent(): Path;
    get internalPathToFirstLeafContent(): Path;
    AddContent(contentObjOrList: InkObject | InkObject[]): void;
    TryAddNamedContent(contentObj: InkObject): void;
    AddToNamedContentOnly(namedContentObj: INamedContent): undefined;
    ContentAtPath(path: Path, partialPathStart?: number, partialPathLength?: number): SearchResult;
    InsertContent(contentObj: InkObject, index: number): void;
    AddContentsOfContainer(otherContainer: Container): void;
    ContentWithPathComponent(component: Path.Component): InkObject | null;
    BuildStringOfHierarchy(): string;
    BuildStringOfHierarchy(sb: StringBuilder, indentation: number, pointedObj: InkObject | null): string;
}
export declare namespace Container {
    enum CountFlags {
        Start = 0,
        Visits = 1,
        Turns = 2,
        CountStartOnly = 4
    }
}
