import { ExecutionGroup, ExecutionNode, ExecutionRunTrace } from "@/lib/execution-trace";

export const PRIMARY_NODE_WIDTH = 214;
export const PRIMARY_NODE_HEIGHT = 154;
export const CHILD_NODE_WIDTH = 152;
export const CHILD_NODE_HEIGHT = 112;

const CHILD_NODE_KINDS = new Set(["tool", "model", "retrieval", "memory"]);
const PRIMARY_Y = 96;
const CHILD_Y = 288;
const CLUSTER_GAP = 24;
const GROUP_BREAK_GAP = 40;
const CHILD_GAP = 12;
const START_X = 40;
const GROUP_PADDING_X = 20;
const GROUP_PADDING_TOP = 26;
const GROUP_PADDING_BOTTOM = 24;

type LayoutLane = "primary" | "child";

export type PositionedExecutionNode = {
  node: ExecutionNode;
  x: number;
  y: number;
  width: number;
  height: number;
  lane: LayoutLane;
  anchorId?: string;
  groupId?: string;
};

export type PositionedExecutionGroup = ExecutionGroup & {
  memberNodeIds: string[];
};

export type ExecutionGraphLayout = {
  nodes: PositionedExecutionNode[];
  groups: PositionedExecutionGroup[];
};

function isChildNode(node: ExecutionNode): boolean {
  return CHILD_NODE_KINDS.has(node.kind);
}

function findContainingGroup(node: ExecutionNode, groups: ExecutionGroup[]): ExecutionGroup | null {
  if (groups.length === 0) {
    return null;
  }
  const centerX = node.x + PRIMARY_NODE_WIDTH / 2;
  const centerY = node.y + PRIMARY_NODE_HEIGHT / 2;

  for (const group of groups) {
    if (
      centerX >= group.x &&
      centerX <= group.x + group.width &&
      centerY >= group.y &&
      centerY <= group.y + group.height
    ) {
      return group;
    }
  }

  return groups
    .slice()
    .sort((left, right) => {
      const leftCenter = left.x + left.width / 2;
      const rightCenter = right.x + right.width / 2;
      return Math.abs(leftCenter - centerX) - Math.abs(rightCenter - centerX);
    })[0] ?? null;
}

function collectGroupMembership(trace: ExecutionRunTrace): Map<string, string | undefined> {
  const membership = new Map<string, string | undefined>();
  for (const node of trace.nodes) {
    membership.set(node.id, findContainingGroup(node, trace.groups)?.id);
  }
  return membership;
}

function findNearestPrimary(
  nodeId: string,
  trace: ExecutionRunTrace,
  primaryIds: Set<string>,
  direction: "upstream" | "downstream"
): string | undefined {
  const nodeMap = new Map(trace.nodes.map((node) => [node.id, node]));
  const queue = [...(nodeMap.get(nodeId)?.[direction] ?? [])];
  const visited = new Set<string>([nodeId]);

  while (queue.length > 0) {
    const currentId = queue.shift();
    if (!currentId || visited.has(currentId)) {
      continue;
    }
    visited.add(currentId);
    if (primaryIds.has(currentId)) {
      return currentId;
    }
    const current = nodeMap.get(currentId);
    if (!current) {
      continue;
    }
    queue.push(...current[direction]);
  }

  return undefined;
}

function buildAnchorMap(trace: ExecutionRunTrace, primaryNodes: ExecutionNode[]): Map<string, string> {
  const primaryIds = new Set(primaryNodes.map((node) => node.id));
  const anchors = new Map<string, string>();

  for (const node of trace.nodes) {
    if (primaryIds.has(node.id)) {
      anchors.set(node.id, node.id);
      continue;
    }
    const upstreamPrimary = findNearestPrimary(node.id, trace, primaryIds, "upstream");
    const downstreamPrimary = findNearestPrimary(node.id, trace, primaryIds, "downstream");
    anchors.set(node.id, upstreamPrimary ?? downstreamPrimary ?? primaryNodes[0]?.id ?? node.id);
  }

  return anchors;
}

function groupNodesByAnchor(trace: ExecutionRunTrace, anchors: Map<string, string>): Map<string, ExecutionNode[]> {
  const buckets = new Map<string, ExecutionNode[]>();
  for (const node of trace.nodes) {
    if (!isChildNode(node)) {
      continue;
    }
    const anchorId = anchors.get(node.id);
    if (!anchorId) {
      continue;
    }
    const bucket = buckets.get(anchorId) ?? [];
    bucket.push(node);
    buckets.set(anchorId, bucket);
  }

  for (const bucket of buckets.values()) {
    bucket.sort((left, right) => trace.nodes.findIndex((node) => node.id === left.id) - trace.nodes.findIndex((node) => node.id === right.id));
  }

  return buckets;
}

function layoutGroups(
  trace: ExecutionRunTrace,
  positionedNodes: PositionedExecutionNode[],
  membership: Map<string, string | undefined>
): PositionedExecutionGroup[] {
  const positionedById = new Map(positionedNodes.map((node) => [node.node.id, node]));

  return trace.groups
    .map((group) => {
      const members = trace.nodes
        .filter((node) => membership.get(node.id) === group.id)
        .map((node) => positionedById.get(node.id))
        .filter((node): node is PositionedExecutionNode => Boolean(node));

      if (members.length === 0) {
        return null;
      }

      const minX = Math.min(...members.map((member) => member.x));
      const maxX = Math.max(...members.map((member) => member.x + member.width));
      const minY = Math.min(...members.map((member) => member.y));
      const maxY = Math.max(...members.map((member) => member.y + member.height));

      return {
        ...group,
        x: minX - GROUP_PADDING_X,
        y: minY - GROUP_PADDING_TOP,
        width: maxX - minX + GROUP_PADDING_X * 2,
        height: maxY - minY + GROUP_PADDING_TOP + GROUP_PADDING_BOTTOM,
        memberNodeIds: members.map((member) => member.node.id),
      };
    })
    .filter((group): group is PositionedExecutionGroup => Boolean(group));
}

export function buildExecutionGraphLayout(trace: ExecutionRunTrace): ExecutionGraphLayout {
  const membership = collectGroupMembership(trace);
  const primaryNodes = trace.nodes.filter((node) => !isChildNode(node));
  const anchors = buildAnchorMap(trace, primaryNodes);
  const childBuckets = groupNodesByAnchor(trace, anchors);
  const positionedNodes: PositionedExecutionNode[] = [];

  let cursorX = START_X;
  let previousGroupId: string | undefined;

  for (const primaryNode of primaryNodes) {
    const currentGroupId = membership.get(primaryNode.id);
    if (previousGroupId && currentGroupId && previousGroupId !== currentGroupId) {
      cursorX += GROUP_BREAK_GAP;
    }

    const children = childBuckets.get(primaryNode.id) ?? [];
    const childClusterWidth =
      children.length > 0
        ? children.length * CHILD_NODE_WIDTH + Math.max(0, children.length - 1) * CHILD_GAP
        : PRIMARY_NODE_WIDTH;
    const clusterWidth = Math.max(PRIMARY_NODE_WIDTH, childClusterWidth);
    const clusterStartX = cursorX;

    positionedNodes.push({
      node: primaryNode,
      x: clusterStartX + (clusterWidth - PRIMARY_NODE_WIDTH) / 2,
      y: PRIMARY_Y,
      width: PRIMARY_NODE_WIDTH,
      height: PRIMARY_NODE_HEIGHT,
      lane: "primary",
      anchorId: primaryNode.id,
      groupId: currentGroupId,
    });

    if (children.length === 1) {
      positionedNodes.push({
        node: children[0],
        x: clusterStartX + (clusterWidth - CHILD_NODE_WIDTH) / 2,
        y: CHILD_Y,
        width: CHILD_NODE_WIDTH,
        height: CHILD_NODE_HEIGHT,
        lane: "child",
        anchorId: primaryNode.id,
        groupId: membership.get(children[0].id),
      });
    } else {
      children.forEach((child, index) => {
        positionedNodes.push({
          node: child,
          x: clusterStartX + index * (CHILD_NODE_WIDTH + CHILD_GAP),
          y: CHILD_Y,
          width: CHILD_NODE_WIDTH,
          height: CHILD_NODE_HEIGHT,
          lane: "child",
          anchorId: primaryNode.id,
          groupId: membership.get(child.id),
        });
      });
    }

    cursorX += clusterWidth + CLUSTER_GAP;
    previousGroupId = currentGroupId;
  }

  const groups = layoutGroups(trace, positionedNodes, membership);

  return {
    nodes: trace.nodes
      .map((node) => positionedNodes.find((positioned) => positioned.node.id === node.id))
      .filter((node): node is PositionedExecutionNode => Boolean(node)),
    groups,
  };
}
