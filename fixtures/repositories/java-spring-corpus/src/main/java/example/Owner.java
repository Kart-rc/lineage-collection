package example;

import jakarta.persistence.Entity;
import jakarta.persistence.Table;

// @Entity class FakeOwner {}
// owners.save(fakeOwner);
@Entity
@Table(name = "owners")
public class Owner {
    private Integer id;
    private String lastName;
    private String example = "class FakeRepository {} owners.save(fakeOwner)";
}
