# Move every element associated with a set of surfaces into one component.
#
# Usage:
#   move_group_by_surface "air-carrier" [list 12 13 14]
#
# The target component must already exist.  The procedure returns the number
# of elements moved, and raises an error when the surfaces have no associated
# elements.
proc move_group_by_surface {component_name surface_ids} {
    if {[llength $surface_ids] == 0} {
        error "surface_ids is empty"
    }

    # Mark all elements associated with the surfaces in one operation.
    *createmark elems 1 "by surface" {*}$surface_ids
    set count [hm_marklength elems 1]
    if {$count == 0} {
        error "no elements are associated with surfaces: $surface_ids"
    }

    # Move the marked elements into the existing component.
    *movemark elems 1 "$component_name"
    return $count
}

# Example:
#   set moved [move_group_by_surface "air-carrier" [list 12 13 14]]
#   puts "moved $moved elements"
