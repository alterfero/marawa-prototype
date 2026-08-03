declare module "d3-force-3d" {
  type NumericAccessor<Node> = number | ((node: Node) => number);
  type LinkEndpoint<Node> = string | number | Node;

  export interface ForceWithStrength<Node> {
    (alpha: number): void;
    initialize?: (nodes: Node[]) => void;
    strength(value: NumericAccessor<Node>): ForceWithStrength<Node>;
  }

  export interface Simulation<Node> {
    stop(): this;
    restart(): this;
    nodes(): Node[];
    alpha(value: number): this;
    alphaTarget(value: number): this;
    alphaDecay(value: number): this;
    force(name: string, force: unknown): this;
    on(type: string, listener: (() => void) | null): this;
  }

  export interface LinkForce<Node, Link extends { source: LinkEndpoint<Node>; target: LinkEndpoint<Node> }> {
    id(accessor: (node: Node) => string | number): this;
    distance(value: NumericAccessor<Link>): this;
    strength(value: NumericAccessor<Link>): this;
  }

  export type CollisionForce<Node> = ForceWithStrength<Node> & {
    radius(value: NumericAccessor<Node>): CollisionForce<Node>;
  };
  export type AxisForce<Node> = ForceWithStrength<Node>;

  export function forceSimulation<Node>(nodes?: Node[], numDimensions?: number): Simulation<Node>;
  export function forceLink<Node, Link extends { source: LinkEndpoint<Node>; target: LinkEndpoint<Node> }>(
    links?: Link[],
  ): LinkForce<Node, Link>;
  export function forceManyBody<Node = unknown>(): ForceWithStrength<Node>;
  export function forceCenter(x?: number, y?: number, z?: number): unknown;
  export function forceCollide<Node = unknown>(radius?: NumericAccessor<Node>): CollisionForce<Node>;
  export function forceX<Node = unknown>(x?: NumericAccessor<Node>): AxisForce<Node>;
  export function forceY<Node = unknown>(y?: NumericAccessor<Node>): AxisForce<Node>;
  export function forceZ<Node = unknown>(z?: NumericAccessor<Node>): AxisForce<Node>;
}

declare module "three" {
  export class Object3D {
    name: string;
    position: {
      x: number;
      y: number;
      z: number;
      set(x: number, y: number, z: number): void;
    };
    rotation: {
      x: number;
      y: number;
      z: number;
    };
  }

  export class Group extends Object3D {
    add(object: Object3D): void;
  }

  export class AmbientLight extends Object3D {
    constructor(color?: string | number, intensity?: number);
  }

  export class DirectionalLight extends Object3D {
    constructor(color?: string | number, intensity?: number);
  }

  export class CanvasTexture {
    constructor(image: HTMLCanvasElement);
    needsUpdate: boolean;
    dispose(): void;
  }

  export class PlaneGeometry {
    constructor(width?: number, height?: number);
    dispose(): void;
  }

  export class SphereGeometry {
    constructor(radius?: number, widthSegments?: number, heightSegments?: number);
    dispose(): void;
  }

  export class MeshBasicMaterial {
    constructor(parameters?: Record<string, unknown>);
    map?: CanvasTexture;
    dispose(): void;
  }

  export class MeshLambertMaterial {
    constructor(parameters?: Record<string, unknown>);
    dispose(): void;
  }

  export class SpriteMaterial {
    constructor(parameters?: Record<string, unknown>);
    dispose(): void;
  }

  export class Mesh extends Object3D {
    constructor(geometry?: PlaneGeometry | SphereGeometry, material?: MeshBasicMaterial | MeshLambertMaterial);
    geometry: {
      dispose(): void;
    };
    material: MeshBasicMaterial | MeshLambertMaterial | Array<MeshBasicMaterial | MeshLambertMaterial>;
    raycast?: (...args: unknown[]) => void | null;
  }

  export class Sprite extends Object3D {
    constructor(material?: SpriteMaterial);
    scale: {
      set(x: number, y: number, z: number): void;
    };
  }
}
