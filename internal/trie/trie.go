package trie

import (
	"fmt"
	"strings"
)

type Tree[Key comparable, Value any] struct {
	value    *Value
	children map[Key]*Tree[Key, Value]
}

func New[Key comparable, Value any]() *Tree[Key, Value] {
	return &Tree[Key, Value]{
		children: map[Key]*Tree[Key, Value]{},
	}
}

func (tree *Tree[Key, Value]) Insert(path []Key, value Value) {
	if tree.children == nil {
		tree.children = map[Key]*Tree[Key, Value]{}
	}

	for i := 0; i < len(path); i++ {
		if child, ok := tree.children[path[i]]; !ok {
			newNode := New[Key, Value]()
			tree.children[path[i]] = newNode
			tree = newNode
		} else {
			tree = child
		}
	}
	tree.value = &value
}

func (tree *Tree[Key, Value]) Find(path []Key) (*Tree[Key, Value], bool) {
	for i := 0; i < len(path); i++ {
		if child, ok := tree.children[path[i]]; !ok {
			return nil, false
		} else {
			tree = child
		}
	}
	return tree, true
}

func (tree *Tree[Key, Value]) CollectLeafValues() []Value {
	result := []Value{}
	switch {
	case len(tree.children) == 0:
		if tree.value != nil {
			return []Value{*tree.value}
		}
		return []Value{}
	default:
		for _, child := range tree.children {
			result = append(result, child.CollectLeafValues()...)
		}
	}
	return result
}

func (tree *Tree[Key, Value]) DebugString() string {
	var b strings.Builder
	tree.writeString(&b, 0)
	return b.String()
}

func (tree *Tree[Key, Value]) writeString(b *strings.Builder, depth int) {
	if tree == nil {
		return
	}

	if tree.value != nil {
		fmt.Fprintf(b, "%*s%v\n", depth*2, "", *tree.value)
	}

	for key, child := range tree.children {
		fmt.Fprintf(b, "%*s%v\n", depth*2, "", key)
		child.writeString(b, depth+1)
	}
}
